"""Vehicle detection + tracking using ultralytics YOLO + built-in ByteTrack."""
from __future__ import annotations

import logging
from typing import Any

from ..utils.geometry import bbox_xyxy_to_normalized
from .tracker import Detection

log = logging.getLogger(__name__)


class VehicleDetector:
    """Phase 1 — detect & track vehicles."""

    def __init__(self, models_cfg: dict[str, Any], tracker_cfg: dict[str, Any]):
        from ultralytics import YOLO  # local import so tests don't need GPU

        self._weights = models_cfg.get("vehicle_weights", "yolov8n.pt")
        self._device = models_cfg.get("device", "cuda:0")
        self._conf = float(models_cfg.get("vehicle_conf", 0.35))
        self._iou = float(models_cfg.get("vehicle_iou", 0.5))
        self._imgsz = int(models_cfg.get("imgsz_vehicle", 640))
        self._classes = list(models_cfg.get("vehicle_classes", [2, 3, 5, 7]))
        self._class_names = {int(k): str(v) for k, v in (models_cfg.get("vehicle_class_names") or {}).items()}
        self._tracker_yaml = str(tracker_cfg.get("type", "bytetrack.yaml"))
        log.info("Loading YOLO vehicle weights=%s device=%s", self._weights, self._device)
        self._model = YOLO(self._weights)

    def track(self, frame, frame_w: int, frame_h: int) -> list[Detection]:
        """Run detection + tracking. Returns list[Detection] (with track_id)."""
        results = self._model.track(
            frame,
            persist=True,
            conf=self._conf,
            iou=self._iou,
            imgsz=self._imgsz,
            classes=self._classes,
            device=self._device,
            tracker=self._tracker_yaml,
            verbose=False,
        )
        detections: list[Detection] = []
        if not results:
            return detections
        result = results[0]
        boxes = getattr(result, "boxes", None)
        if boxes is None or boxes.xyxy is None:
            return detections
        xyxy = boxes.xyxy.cpu().numpy().tolist()
        confs = boxes.conf.cpu().numpy().tolist() if boxes.conf is not None else [0.0] * len(xyxy)
        clss = boxes.cls.cpu().numpy().tolist() if boxes.cls is not None else [0] * len(xyxy)
        ids = (
            boxes.id.cpu().numpy().astype(int).tolist()
            if getattr(boxes, "id", None) is not None
            else [-1] * len(xyxy)
        )
        for i, box in enumerate(xyxy):
            class_id = int(clss[i])
            name = self._class_names.get(class_id) or self._model.names.get(class_id, str(class_id))
            detections.append(
                Detection(
                    track_id=int(ids[i]) if i < len(ids) else -1,
                    class_id=class_id,
                    class_name=str(name),
                    confidence=float(confs[i]) if i < len(confs) else 0.0,
                    bbox_xyxy=[float(v) for v in box],
                    bbox_norm=bbox_xyxy_to_normalized(box, frame_w, frame_h),
                )
            )
        return detections
