"""Detection dataclass, per-track state manager and OC-SORT tracker wrapper."""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from ..utils.geometry import bbox_xyxy_to_normalized

log = logging.getLogger(__name__)


@dataclass
class PlateInfo:
    bbox_xyxy: list[float] = field(default_factory=list)   # absolute pixel xyxy
    bbox_norm: list[float] = field(default_factory=list)   # normalized 0..1
    text: str = ""
    confidence: float = 0.0


@dataclass
class Detection:
    track_id: int
    class_id: int
    class_name: str
    confidence: float
    bbox_xyxy: list[float]          # absolute pixel xyxy
    bbox_norm: list[float]          # normalized 0..1
    plate: PlateInfo | None = None


class TrackStateManager:
    """Thread-safe per-track_id state for OCR cooldown + cached recognised text."""

    def __init__(self, ocr_cooldown_seconds: float = 3.0, buffer_seconds: float = 5.0):
        self._lock = threading.Lock()
        self._last_ocr_ts: dict[int, float] = {}
        self._text_cache: dict[int, dict[str, Any]] = {}
        self._last_seen: dict[int, float] = {}
        self._cooldown = float(ocr_cooldown_seconds)
        self._buffer = float(buffer_seconds)

    def touch(self, track_id: int) -> None:
        with self._lock:
            self._last_seen[track_id] = time.time()

    def should_ocr(self, track_id: int) -> bool:
        now = time.time()
        with self._lock:
            cached = self._text_cache.get(track_id)
            if cached and cached.get("text"):
                # Already recognised — re-OCR only after a full cooldown.
                if now - self._last_ocr_ts.get(track_id, 0.0) < self._cooldown * 3:
                    return False
            last = self._last_ocr_ts.get(track_id, 0.0)
            return (now - last) >= self._cooldown

    def mark_ocr_attempt(self, track_id: int) -> None:
        with self._lock:
            self._last_ocr_ts[track_id] = time.time()

    def store_text(self, track_id: int, text: str, confidence: float) -> None:
        if not text:
            return
        with self._lock:
            cached = self._text_cache.get(track_id)
            if cached and cached.get("confidence", 0.0) >= confidence:
                return
            self._text_cache[track_id] = {"text": text, "confidence": float(confidence)}

    def get_text(self, track_id: int) -> tuple[str, float]:
        with self._lock:
            cached = self._text_cache.get(track_id)
        if not cached:
            return "", 0.0
        return cached.get("text", ""), float(cached.get("confidence", 0.0))

    def prune(self) -> None:
        """Drop state for tracks not seen recently."""
        cutoff = time.time() - self._buffer
        with self._lock:
            stale = [tid for tid, ts in self._last_seen.items() if ts < cutoff]
            for tid in stale:
                self._last_seen.pop(tid, None)
                self._last_ocr_ts.pop(tid, None)
                self._text_cache.pop(tid, None)



class OcSortTracker:
    """OC-SORT tracker wrapper using boxmot.

    Takes raw Detection objects from VehicleDetector, returns the same list but
    with assigned ``track_id``. Detections that the tracker drops (low confidence,
    failed association) are filtered out.
    """

    def __init__(self, tracker_cfg: dict[str, Any]):
        import numpy as np

        try:
            from boxmot import OcSort as _OcSort  # type: ignore
        except ImportError:
            try:
                from boxmot.trackers.ocsort.ocsort import OcSort as _OcSort  # type: ignore  # noqa: F401
            except ImportError:
                try:
                    from boxmot.trackers.ocsort.ocsort import OCSORT as _OcSort  # type: ignore
                except ImportError as exc:
                    raise ImportError(
                        "boxmot not installed. Run: pip install boxmot"
                    ) from exc

        self._np = np
        params = tracker_cfg.get("ocsort", {}) or {}
        kwargs = dict(
            det_thresh=float(params.get("det_thresh", 0.3)),
            max_age=int(params.get("max_age", 30)),
            min_hits=int(params.get("min_hits", 3)),
            asso_threshold=float(params.get("asso_threshold", 0.3)),
            delta_t=int(params.get("delta_t", 3)),
            asso_func=str(params.get("asso_func", "iou")),
            inertia=float(params.get("inertia", 0.2)),
            use_byte=bool(params.get("use_byte", False)),
        )
        try:
            self._tracker = _OcSort(**kwargs)
        except TypeError:
            # Older API uses iou_threshold instead of asso_threshold
            kwargs["iou_threshold"] = kwargs.pop("asso_threshold")
            self._tracker = _OcSort(**kwargs)
        log.info("OC-SORT tracker initialised: %s", kwargs)

    def update(self, detections: list[Detection], frame) -> list[Detection]:
        np = self._np
        h, w = frame.shape[:2]
        if not detections:
            try:
                self._tracker.update(np.empty((0, 6)), frame)
            except Exception:
                pass
            return []
        dets_arr = np.array(
            [[*d.bbox_xyxy, d.confidence, d.class_id] for d in detections],
            dtype=np.float32,
        )
        try:
            tracks = self._tracker.update(dets_arr, frame)
        except Exception as exc:  # noqa: BLE001
            log.exception("OC-SORT update error: %s", exc)
            return []
        if tracks is None or len(tracks) == 0:
            return []
        tracks = np.asarray(tracks)
        result: list[Detection] = []
        for row in tracks:
            if len(row) < 5:
                continue
            x1, y1, x2, y2 = (float(v) for v in row[0:4])
            tid = int(row[4])
            conf = float(row[5]) if len(row) > 5 else 0.0
            cls = int(row[6]) if len(row) > 6 else -1
            det_idx = int(row[7]) if len(row) > 7 and not np.isnan(row[7]) else -1

            class_name = "vehicle"
            if 0 <= det_idx < len(detections):
                src = detections[det_idx]
                class_name = src.class_name
                if cls < 0:
                    cls = src.class_id
                if conf <= 0.0:
                    conf = src.confidence
            else:
                for src in detections:
                    if src.class_id == cls:
                        class_name = src.class_name
                        break

            bbox = [x1, y1, x2, y2]
            result.append(
                Detection(
                    track_id=tid,
                    class_id=cls if cls >= 0 else 0,
                    class_name=class_name,
                    confidence=conf,
                    bbox_xyxy=bbox,
                    bbox_norm=bbox_xyxy_to_normalized(bbox, w, h),
                )
            )
        return result
