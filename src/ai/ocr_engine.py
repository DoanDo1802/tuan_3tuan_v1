"""Phase 3 — PaddleOCR recognition worker (Thread 3)."""
from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any

import cv2

from ..utils.validation import is_valid_plate, normalize_plate
from .tracker import TrackStateManager

log = logging.getLogger(__name__)


@dataclass
class OcrJob:
    track_id: int
    plate_crop: Any           # BGR ndarray cropped tightly to the plate
    ts: float


class OcrEngine(threading.Thread):
    """Consume plate crops from a queue, run PaddleOCR, update TrackStateManager."""

    def __init__(
        self,
        ocr_cfg: dict[str, Any],
        ocr_queue: "queue.Queue[OcrJob]",
        track_state: TrackStateManager,
        stop_event: threading.Event,
    ):
        super().__init__(name="OcrEngine", daemon=True)
        self._cfg = ocr_cfg
        self._queue = ocr_queue
        self._state = track_state
        # Tránh shadow Thread._stop() (private method của threading.Thread).
        self._stop_event = stop_event
        self._engine = None
        self._enabled = bool(ocr_cfg.get("enabled", True))
        self._min_conf = float(ocr_cfg.get("min_confidence", 0.5))
        self._min_h = int(ocr_cfg.get("min_plate_height_px", 16))

    def _lazy_init(self) -> None:
        if self._engine is not None or not self._enabled:
            return
        try:
            from paddleocr import PaddleOCR
        except Exception as exc:
            log.error("PaddleOCR import failed, OCR disabled: %s", exc)
            self._enabled = False
            return
        # PaddleOCR API thay đổi nhiều giữa các version (2.x vs 3.x).
        # Thử dần từ full args → minimal để tương thích ngược.
        attempts = [
            dict(
                lang=str(self._cfg.get("lang", "en")),
                use_gpu=bool(self._cfg.get("use_gpu", True)),
                show_log=False,
                det=bool(self._cfg.get("det", False)),
                rec=bool(self._cfg.get("rec", True)),
                cls=bool(self._cfg.get("cls", False)),
            ),
            dict(
                lang=str(self._cfg.get("lang", "en")),
                use_gpu=bool(self._cfg.get("use_gpu", True)),
                det=bool(self._cfg.get("det", False)),
                rec=bool(self._cfg.get("rec", True)),
                cls=bool(self._cfg.get("cls", False)),
            ),
            dict(
                lang=str(self._cfg.get("lang", "en")),
                use_gpu=bool(self._cfg.get("use_gpu", True)),
            ),
            dict(lang=str(self._cfg.get("lang", "en"))),
        ]
        last_exc: Exception | None = None
        for kw in attempts:
            try:
                self._engine = PaddleOCR(**kw)
                log.info("PaddleOCR initialised with kwargs=%s", list(kw.keys()))
                return
            except Exception as exc:
                last_exc = exc
                self._engine = None
        log.error("PaddleOCR init failed, OCR disabled: %s", last_exc)
        self._enabled = False

    def run(self) -> None:
        self._lazy_init()
        if not self._enabled:
            log.info("OCR disabled, OcrEngine idle loop")
            self._stop_event.wait()
            return
        while not self._stop_event.is_set():
            try:
                job = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            self._process(job)
        log.info("OcrEngine stopped")

    def _process(self, job: OcrJob) -> None:
        crop = job.plate_crop
        if crop is None or getattr(crop, "size", 0) == 0:
            return
        h = crop.shape[0]
        if h < self._min_h:
            return
        crop = self._preprocess(crop)
        # PaddleOCR v3 bỏ kwargs det/rec/cls; gọi positional cho an toàn.
        try:
            result = self._engine.ocr(crop, det=False, rec=True, cls=False)
        except TypeError:
            try:
                result = self._engine.ocr(crop)
            except Exception as exc:
                log.warning("PaddleOCR ocr() error: %s", exc)
                return
        except Exception as exc:
            log.warning("PaddleOCR ocr() error: %s", exc)
            return
        text, confidence = self._extract_best(result)
        if not text:
            return
        cleaned = normalize_plate(text)
        if not cleaned or confidence < self._min_conf:
            return
        if not is_valid_plate(cleaned):
            # Still store, but with reduced confidence so other valid hits can override.
            confidence *= 0.5
        self._state.store_text(job.track_id, cleaned, confidence)
        log.info("OCR track=%s text=%s conf=%.2f", job.track_id, cleaned, confidence)

    @staticmethod
    def _preprocess(crop):
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        h = gray.shape[0]
        if h < 32:
            scale = 32.0 / h
            gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        gray = cv2.bilateralFilter(gray, 5, 30, 30)
        return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)

    @staticmethod
    def _extract_best(result) -> tuple[str, float]:
        best_text = ""
        best_conf = 0.0
        if not result:
            return best_text, best_conf
        # PaddleOCR returns nested list shapes depending on det/rec mode.
        for line in result:
            if line is None:
                continue
            if isinstance(line, (list, tuple)) and line and isinstance(line[0], (list, tuple)):
                for item in line:
                    text, conf = OcrEngine._unwrap_item(item)
                    if conf > best_conf:
                        best_text, best_conf = text, conf
            else:
                text, conf = OcrEngine._unwrap_item(line)
                if conf > best_conf:
                    best_text, best_conf = text, conf
        return best_text, best_conf

    @staticmethod
    def _unwrap_item(item) -> tuple[str, float]:
        if not item:
            return "", 0.0
        # rec-only: ("text", conf). det+rec: [box, ("text", conf)].
        if isinstance(item, (list, tuple)) and len(item) == 2 and isinstance(item[0], str):
            return str(item[0]), float(item[1] or 0.0)
        if isinstance(item, (list, tuple)) and len(item) >= 2 and isinstance(item[1], (list, tuple)):
            inner = item[1]
            if len(inner) >= 2:
                return str(inner[0]), float(inner[1] or 0.0)
        return "", 0.0
