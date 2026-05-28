"""Pipeline điều phối toàn bộ luồng xử lý real-time."""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any

from .ai.ocr_engine import OcrEngine, OcrJob
from .ai.plate_detector import PlateDetector
from .ai.tracker import Detection, OcSortTracker, PlateInfo, TrackStateManager
from .ai.vehicle_detector import VehicleDetector
from .config_loader import Config
from .mqtt.discovery import CameraDiscovery
from .mqtt.publisher import BboxPublisher
from .mqtt.zones import ZoneManager
from .utils.geometry import (
    bbox_center,
    bbox_xyxy_to_normalized,
    normalize_polygon,
    point_in_polygon,
    polygon_pixel_bounding_rect,
)
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
        # Mỗi zone_id có một tracker riêng để track_id không bị trùng giữa các vùng.
        self._trackers: dict[str, OcSortTracker] = {}
        self._ai_thread: threading.Thread | None = None

        zf = cfg.zone_filter
        self._crop_before_detect = bool(zf.get("crop_before_detect", True))
        self._crop_padding = int(zf.get("crop_padding_pixels", 30))
        self._fallback_full_frame = bool(zf.get("fallback_full_frame_if_no_zone", True))
        # Cờ one-shot để log diagnostics lần đầu tiên (tránh spam log)
        self._logged_frame_shape = False
        self._logged_first_track = False
        self._logged_plate_state = False
        self._logged_plate_attempt = False
        self._logged_first_plate = False
        self._timing_counter = 0

    # Vòng đời pipeline -------------------------------------------------------
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

        self.vehicle_detector = VehicleDetector(self.cfg.models)
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

    # Xác định URL RTSP để kết nối camera ------------------------------------
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

    # Thread AI phát hiện & tracking xe (Thread 2) --------------------------
    def _ai_loop(self) -> None:
        log.info("AI detection thread started")
        cam_code = self.cfg.camera_code
        ai_module = self.cfg.ai_module
        while not self.stop_event.is_set():
            try:
                ts, frame = self.frame_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            # Drain backlog: nếu decoder đẩy frame nhanh hơn AI, bỏ frame cũ
            # và lấy frame mới nhất để duy trì cảm giác real-time.
            dropped = 0
            while True:
                try:
                    ts, frame = self.frame_queue.get_nowait()
                    dropped += 1
                except queue.Empty:
                    break
            if dropped:
                log.debug("AI loop dropped %d stale frames", dropped)
            h, w = frame.shape[:2]
            if not self._logged_frame_shape:
                log.info("AI loop first frame shape: %dx%d (HxW)", h, w)
                self._logged_frame_shape = True
            if not self._logged_plate_state:
                ready = self.plate_detector is not None and self.plate_detector.ready
                log.info("Plate detector ready=%s", ready)
                self._logged_plate_state = True
            t_start = time.time()
            frame_age_ms = (t_start - ts) * 1000.0
            try:
                detections = self._detect_and_track(frame, w, h, cam_code)
            except Exception as exc:
                log.exception("Vehicle detection / tracking error: %s", exc)
                continue
            t_detect = time.time()
            kept: list[Detection] = []
            for det in detections:
                if det.track_id < 0:
                    continue
                self.track_state.touch(det.track_id)
                if not self._logged_first_track:
                    log.info(
                        "First track: id=%s cls=%s bbox_xyxy=%s bbox_norm=%s",
                        det.track_id, det.class_name,
                        [round(v, 1) for v in det.bbox_xyxy],
                        [round(v, 4) for v in det.bbox_norm],
                    )
                    self._logged_first_track = True
                self._attach_plate(frame, det, w, h)
                kept.append(det)
            t_plate = time.time()
            self._emit(cam_code, ai_module, ts, kept)
            # Log periodic timing để chẩn đoán độ trễ.
            self._timing_counter += 1
            if self._timing_counter % 60 == 0:
                detect_ms = (t_detect - t_start) * 1000.0
                plate_ms = (t_plate - t_detect) * 1000.0
                log.info(
                    "Timing[every 60 frames]: frame_age=%.0fms detect=%.0fms plate=%.0fms dropped=%d n_det=%d",
                    frame_age_ms, detect_ms, plate_ms, dropped, len(kept),
                )
        log.info("AI detection thread stopped")
        log.info("AI detection thread stopped")

    # Phát hiện xe + gán track_id theo từng zone ------------------------------
    def _detect_and_track(
        self, frame, frame_w: int, frame_h: int, cam_code: str
    ) -> list[Detection]:
        """Trả về danh sách Detection đã được gán track_id.

        Hành vi:
          * Nếu có zone và crop_before_detect=True: crop bounding rect của từng
            polygon rồi chạy YOLO trên vùng nhỏ đó, sau đó chuyển bbox về tọa độ
            full-frame. Mỗi zone_id dùng một tracker riêng.
          * Nếu có zone nhưng crop_before_detect=False: chạy YOLO trên full frame
            rồi lọc detection bằng point_in_polygon.
          * Nếu không có zone: tùy config có thể fallback về detect toàn frame.
        """
        zone_entries = self._collect_zones(cam_code, frame_w, frame_h)
        if not zone_entries:
            if not self._fallback_full_frame:
                return []
            raw = self.vehicle_detector.detect(frame, frame_w, frame_h)
            return self._get_tracker("__fullframe__").update(raw, frame)

        if not self._crop_before_detect:
            raw = self.vehicle_detector.detect(frame, frame_w, frame_h)
            filtered = [
                d for d in raw
                if any(
                    point_in_polygon(*bbox_center(d.bbox_norm), poly)
                    for _, poly in zone_entries
                )
            ]
            return self._get_tracker("__fullframe__").update(filtered, frame)

        merged: list[Detection] = []
        for zone_id, polygon in zone_entries:
            x1, y1, x2, y2 = polygon_pixel_bounding_rect(
                polygon, frame_w, frame_h, padding=self._crop_padding
            )
            if x2 - x1 < 16 or y2 - y1 < 16:
                continue
            crop = frame[y1:y2, x1:x2]
            crop_h, crop_w = crop.shape[:2]
            raw = self.vehicle_detector.detect(crop, crop_w, crop_h)
            translated: list[Detection] = []
            for d in raw:
                bx1, by1, bx2, by2 = d.bbox_xyxy
                fb = [bx1 + x1, by1 + y1, bx2 + x1, by2 + y1]
                d.bbox_xyxy = fb
                d.bbox_norm = bbox_xyxy_to_normalized(fb, frame_w, frame_h)
                translated.append(d)
            in_poly = [
                d for d in translated
                if point_in_polygon(*bbox_center(d.bbox_norm), polygon)
            ]
            merged.extend(self._get_tracker(zone_id).update(in_poly, frame))
        return merged

    def _collect_zones(
        self, cam_code: str, frame_w: int, frame_h: int
    ) -> list[tuple[str, list[list[float]]]]:
        result: list[tuple[str, list[list[float]]]] = []
        for idx, z in enumerate(self.zones.zones_for(cam_code)):
            polygon = z.get("polygon") or []
            if len(polygon) < 3:
                continue
            zone_id = str(z.get("zone_id") or f"zone_{idx}")
            poly_norm = normalize_polygon(polygon, frame_w, frame_h)
            result.append((zone_id, poly_norm))
        return result

    def _get_tracker(self, key: str) -> OcSortTracker:
        tracker = self._trackers.get(key)
        if tracker is None:
            tracker = OcSortTracker(self.cfg.tracker)
            self._trackers[key] = tracker
            log.info("Created OC-SORT tracker for key=%s", key)
        return tracker

    # Phát hiện biển số + đẩy job vào hàng đợi OCR -------------------------
    def _attach_plate(self, frame, det: Detection, frame_w: int, frame_h: int) -> None:
        if self.plate_detector is None or not self.plate_detector.ready:
            return
        x1, y1, x2, y2 = (int(max(0, v)) for v in det.bbox_xyxy)
        x2 = min(frame_w, x2)
        y2 = min(frame_h, y2)
        roi_w, roi_h = x2 - x1, y2 - y1
        if roi_w < 16 or roi_h < 16:
            return
        roi = frame[y1:y2, x1:x2]
        plates = self.plate_detector.detect_in_roi(roi, x1, y1, frame_w, frame_h)
        if not self._logged_plate_attempt:
            log.info(
                "Plate attempt: vehicle_roi=%dx%d found=%d",
                roi_w, roi_h, len(plates),
            )
            self._logged_plate_attempt = True
        if not plates:
            return
        if not self._logged_first_plate:
            best = plates[0]
            log.info(
                "First plate: bbox_xyxy=%s conf=%.3f roi=%dx%d",
                [round(v, 1) for v in best["bbox_xyxy"]],
                best["confidence"], roi_w, roi_h,
            )
            self._logged_first_plate = True
        best = plates[0]
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

    # Đóng gói payload JSON và đưa vào publish_queue -------------------------
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
            payload_dets.append(item)

            # Biển số được publish như một detection riêng trong mảng detections
            if det.plate is not None:
                if det.plate.text or self.cfg.publish.get("include_plate_when_unknown", True):
                    plate_item = {
                        "id": f"plate_track_{det.track_id}",
                        "cls": "plate",
                        "class": "plate",
                        "label": "plate",
                        "bbox": [round(v, 4) for v in det.plate.bbox_norm],
                        "text": det.plate.text or "",
                        "confidence": round(det.plate.confidence, 4),
                        "color": "#ffff00",  # Yellow for plates
                    }
                    payload_dets.append(plate_item)
        now = time.time()
        message = {
            "camera_code": cam_code,
            "ai_module": ai_module,
            "ai_modules": [ai_module],
            "timestamp": ts,
            "frame_ts": ts,
            "publish_ts": now,
            "pipeline_latency_ms": round((now - ts) * 1000.0, 1),
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
