"""End-to-end realtime pipeline orchestrator (Thread 2 lives here)."""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any

from .ai.ocr_engine import OcrEngine, OcrJob
from .ai.plate_detector import PlateDetector
from .ai.tracker import Detection, TrackStateManager
from .ai.vehicle_detector import VehicleDetector
from .config_loader import Config
from .mqtt.discovery import CameraDiscovery
from .mqtt.publisher import BboxPublisher
from .mqtt.zones import ZoneManager
from .utils.geometry import bbox_center
from .video.gstreamer_decode import VideoDecoder

log = logging.getLogger(__name__)

_COLOR_BY_CLASS = {
    "car": "#00ff00",
    "motorcycle": "#00aaff",
    "bus": "#ffaa00",
    "truck": "#ff5500",
}


class Pipeline:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.stop_event = threading.Event()
        self.frame_queue: queue.Queue[tuple[float, Any]] = queue.Queue(
            maxsize=int(cfg.video.get("frame_queue_size", 4))
        )
        self.publish_queue: queue.Queue[dict[str, Any]] = queue.Queue(
            maxsize=int(cfg.publish.get("detection_queue_size", 32))
        )
        self.ocr_queue: queue.Queue[OcrJob] = queue.Queue(
            maxsize=int(cfg.ocr.get("max_queue_size", 8))
        )
        self.track_state = TrackStateManager(
            ocr_cooldown_seconds=float(cfg.tracker.get("ocr_cooldown_seconds", 3.0)),
            buffer_seconds=float(cfg.tracker.get("track_buffer_seconds", 5.0)),
        )
        self.discovery = CameraDiscovery(cfg.mqtt, cfg.cameras_topic(), cfg.ai_module)
        self.zones = ZoneManager(cfg.mqtt, cfg.zones_topic_wildcard(), cfg.ai_module)
        self.publisher: BboxPublisher | None = None
        self.decoder: VideoDecoder | None = None
        self.ocr: OcrEngine | None = None
        self.vehicle_detector: VehicleDetector | None = None
        self.plate_detector: PlateDetector | None = None
        self._ai_thread: threading.Thread | None = None

    # Lifecycle -------------------------------------------------------------
    def run(self) -> None:
        self.discovery.start()
        self.zones.start()
        if not self.discovery.wait_first(timeout=10.0):
            log.warning("No camera list received within 10s; continuing anyway")
        rtsp_url = self._resolve_rtsp()
        if not rtsp_url:
            raise RuntimeError(
                f"Cannot resolve RTSP for camera_code={self.cfg.camera_code}. "
                f"Set camera.rtsp_override or ensure the camera is ONLINE on broker."
            )
        log.info("Using RTSP: %s", rtsp_url)

        self.vehicle_detector = VehicleDetector(self.cfg.models, self.cfg.tracker)
        self.plate_detector = PlateDetector(self.cfg.models)

        self.publisher = BboxPublisher(
            self.cfg.mqtt,
            self.cfg.bbox_topic(),
            self.publish_queue,
            self.stop_event,
            publish_interval=float(self.cfg.publish.get("publish_interval_seconds", 0.04)),
        )
        self.publisher.start()

        self.ocr = OcrEngine(self.cfg.ocr, self.ocr_queue, self.track_state, self.stop_event)
        self.ocr.start()

        self.decoder = VideoDecoder(rtsp_url, self.cfg.video, self.frame_queue, self.stop_event)
        self.decoder.start()

        self._ai_thread = threading.Thread(target=self._ai_loop, name="AIDetection", daemon=True)
        self._ai_thread.start()

        try:
            while not self.stop_event.is_set():
                time.sleep(0.5)
                self.track_state.prune()
        except KeyboardInterrupt:
            log.info("KeyboardInterrupt; shutting down")
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        self.stop_event.set()
        for t in (self._ai_thread, self.decoder, self.publisher, self.ocr):
            if t is not None and t.is_alive():
                t.join(timeout=3.0)
        self.discovery.stop()
        self.zones.stop()

    # RTSP resolution -------------------------------------------------------
    def _resolve_rtsp(self) -> str:
        if self.cfg.rtsp_override:
            return self.cfg.rtsp_override
        cam = self.discovery.find_by_code(self.cfg.camera_code)
        if cam is None:
            cams = self.discovery.cameras()
            log.error(
                "camera_code=%s not found among %d ONLINE cameras: %s",
                self.cfg.camera_code,
                len(cams),
                [c.get("code") for c in cams],
            )
            return ""
        return str(cam.get("rtsp") or "")

    # AI thread (Thread 2) --------------------------------------------------
    def _ai_loop(self) -> None:
        log.info("AI detection thread started")
        cam_code = self.cfg.camera_code
        ai_module = self.cfg.ai_module
        while not self.stop_event.is_set():
            try:
                ts, frame = self.frame_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            h, w = frame.shape[:2]
            try:
                detections = self.vehicle_detector.track(frame, w, h)
            except Exception as exc:
                log.exception("Vehicle detection error: %s", exc)
                continue
            kept: list[Detection] = []
            for det in detections:
                if det.track_id < 0:
                    continue
                cx, cy = bbox_center(det.bbox_norm)
                if not self.zones.point_in_active_zones(cam_code, cx, cy, w, h):
                    continue
                self.track_state.touch(det.track_id)
                self._attach_plate(frame, det, w, h)
                kept.append(det)
            self._emit(cam_code, ai_module, ts, kept)
        log.info("AI detection thread stopped")

    # Plate detection + OCR dispatch ---------------------------------------
    def _attach_plate(self, frame, det: Detection, frame_w: int, frame_h: int) -> None:
        if self.plate_detector is None or not self.plate_detector.ready:
            return
        x1, y1, x2, y2 = (int(max(0, v)) for v in det.bbox_xyxy)
        x2 = min(frame_w, x2)
        y2 = min(frame_h, y2)
        if x2 - x1 < 16 or y2 - y1 < 16:
            return
        roi = frame[y1:y2, x1:x2]
        plates = self.plate_detector.detect_in_roi(roi, x1, y1, frame_w, frame_h)
        if not plates:
            return
        best = plates[0]
        from .ai.tracker import PlateInfo
        plate_info = PlateInfo(
            bbox_xyxy=best["bbox_xyxy"],
            bbox_norm=best["bbox_norm"],
            confidence=best["confidence"],
        )
        text, conf = self.track_state.get_text(det.track_id)
        if text:
            plate_info.text = text
            plate_info.confidence = max(plate_info.confidence, conf)
        det.plate = plate_info
        self._maybe_enqueue_ocr(frame, det)

    def _maybe_enqueue_ocr(self, frame, det: Detection) -> None:
        if self.ocr is None or not self.cfg.ocr.get("enabled", True):
            return
        if det.plate is None:
            return
        if not self.track_state.should_ocr(det.track_id):
            return
        px1, py1, px2, py2 = (int(max(0, v)) for v in det.plate.bbox_xyxy)
        h, w = frame.shape[:2]
        px2 = min(w, px2)
        py2 = min(h, py2)
        if px2 - px1 < 8 or py2 - py1 < 8:
            return
        crop = frame[py1:py2, px1:px2].copy()
        job = OcrJob(track_id=det.track_id, plate_crop=crop, ts=time.time())
        try:
            self.ocr_queue.put_nowait(job)
            self.track_state.mark_ocr_attempt(det.track_id)
        except queue.Full:
            pass

    # Build & enqueue MQTT payload -----------------------------------------
    def _emit(self, cam_code: str, ai_module: str, ts: float, detections: list[Detection]) -> None:
        payload_dets: list[dict[str, Any]] = []
        for det in detections:
            item = {
                "id": f"{det.class_name}_track_{det.track_id}",
                "cls": det.class_name,
                "class": det.class_name,
                "label": det.class_name,
                "class_id": det.class_id,
                "confidence": round(det.confidence, 4),
                "bbox": [round(v, 4) for v in det.bbox_norm],
                "color": _COLOR_BY_CLASS.get(det.class_name, "#00ff00"),
            }
            if det.plate is not None:
                plate_payload = {
                    "id": f"plate_track_{det.track_id}",
                    "bbox": [round(v, 4) for v in det.plate.bbox_norm],
                    "text": det.plate.text or "",
                    "confidence": round(det.plate.confidence, 4),
                }
                if det.plate.text or self.cfg.publish.get("include_plate_when_unknown", True):
                    item["plate"] = plate_payload
            payload_dets.append(item)
        message = {
            "camera_code": cam_code,
            "ai_module": ai_module,
            "ai_modules": [ai_module],
            "timestamp": ts,
            "detections": payload_dets,
        }
        try:
            self.publish_queue.put_nowait(message)
        except queue.Full:
            try:
                self.publish_queue.get_nowait()
                self.publish_queue.put_nowait(message)
            except (queue.Empty, queue.Full):
                pass
