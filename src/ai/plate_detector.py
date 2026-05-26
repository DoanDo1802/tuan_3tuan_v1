"""Phase 2 — license plate detection nested inside a Vehicle ROI."""
from __future__ import annotations

import logging
import os
from typing import Any

from ..utils.geometry import bbox_xyxy_to_normalized

log = logging.getLogger(__name__)


class PlateDetector:
    """YOLO model that runs only inside vehicle ROI crops."""

    def __init__(self, models_cfg: dict[str, Any]):
        self._weights = models_cfg.get("plate_weights", "models/best.pt")
        self._device = models_cfg.get("device", "cuda:0")
        self._conf = float(models_cfg.get("plate_conf", 0.25))
        self._iou = float(models_cfg.get("plate_iou", 0.45))
        self._imgsz = int(models_cfg.get("imgsz_plate", 320))
        self._model = None
        if not os.path.exists(self._weights):
            log.warning(
                "Plate weights not found at %s — PlateDetector will return empty.",
                self._weights,
            )
            return
        from ultralytics import YOLO

        log.info("Loading YOLO plate weights=%s device=%s", self._weights, self._device)
        self._model = YOLO(self._weights)

    @property
    def ready(self) -> bool:
        return self._model is not None

    def detect_in_roi(
        self,
        vehicle_roi,
        roi_x1: int,
        roi_y1: int,
        frame_w: int,
        frame_h: int,
    ) -> list[dict[str, Any]]:
        """Detect plates inside one Vehicle ROI crop.

        Returns plates in absolute pixel coords relative to the FULL frame, plus
        normalized 0..1 bbox.
        """
        if self._model is None or vehicle_roi is None or vehicle_roi.size == 0:
            return []
        results = self._model.predict(
            vehicle_roi,
            conf=self._conf,
            iou=self._iou,
            imgsz=self._imgsz,
            device=self._device,
            verbose=False,
        )
        plates: list[dict[str, Any]] = []
        if not results:
            return plates
        result = results[0]
        boxes = getattr(result, "boxes", None)
        if boxes is None or boxes.xyxy is None:
            return plates
        xyxy = boxes.xyxy.cpu().numpy().tolist()
        confs = boxes.conf.cpu().numpy().tolist() if boxes.conf is not None else [0.0] * len(xyxy)
        for i, box in enumerate(xyxy):
            x1 = float(box[0]) + roi_x1
            y1 = float(box[1]) + roi_y1
            x2 = float(box[2]) + roi_x1
            y2 = float(box[3]) + roi_y1
            plates.append({
                "bbox_xyxy": [x1, y1, x2, y2],
                "bbox_norm": bbox_xyxy_to_normalized([x1, y1, x2, y2], frame_w, frame_h),
                "confidence": float(confs[i]) if i < len(confs) else 0.0,
            })
        # Keep the most confident plate per vehicle (tight coupling requirement).
        plates.sort(key=lambda p: p["confidence"], reverse=True)
        return plates
