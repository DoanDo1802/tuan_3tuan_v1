"""Detection dataclass + per-track state manager (OCR cooldown, cached text)."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any


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
