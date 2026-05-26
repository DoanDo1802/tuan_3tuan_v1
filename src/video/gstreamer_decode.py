"""RTSP decode thread using GStreamer (preferred) with OpenCV fallback."""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any

import cv2

log = logging.getLogger(__name__)


def _build_gst_pipeline(template: str, url: str) -> str:
    pipe = template.format(url=url)
    return " ".join(pipe.split())


def _open_capture(url: str, cfg: dict[str, Any]) -> cv2.VideoCapture:
    use_gst = bool(cfg.get("use_gstreamer", True))
    if use_gst:
        pipe = _build_gst_pipeline(cfg.get("pipeline_template", ""), url)
        cap = cv2.VideoCapture(pipe, cv2.CAP_GSTREAMER)
        if cap.isOpened():
            log.info("Opened RTSP via GStreamer pipeline")
            return cap
        log.warning("GStreamer pipeline failed, falling back to OpenCV FFMPEG backend")
        try:
            cap.release()
        except Exception:
            pass
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    if cap.isOpened():
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        log.info("Opened RTSP via OpenCV/FFMPEG backend")
    return cap


class VideoDecoder(threading.Thread):
    """Thread 1: read frames from RTSP and push to a frame queue.

    Drops old frames (queue size 1) to keep the pipeline real-time.
    """

    def __init__(
        self,
        url: str,
        video_cfg: dict[str, Any],
        frame_queue: "queue.Queue[tuple[float, Any]]",
        stop_event: threading.Event,
    ):
        super().__init__(name="VideoDecoder", daemon=True)
        self._url = url
        self._cfg = video_cfg
        self._queue = frame_queue
        self._stop = stop_event
        self._frame_count = 0
        self._last_frame_ts = 0.0
        self._target_fps = float(video_cfg.get("target_fps", 0) or 0)
        self._min_interval = 1.0 / self._target_fps if self._target_fps > 0 else 0.0
        self._read_timeout = float(video_cfg.get("read_timeout_seconds", 5.0))
        self._reconnect = float(video_cfg.get("reconnect_seconds", 3.0))

    @property
    def frame_count(self) -> int:
        return self._frame_count

    def run(self) -> None:
        log.info("VideoDecoder starting url=%s", self._url)
        while not self._stop.is_set():
            cap = _open_capture(self._url, self._cfg)
            if not cap.isOpened():
                log.warning("Cannot open stream, retrying in %ss", self._reconnect)
                if self._stop.wait(self._reconnect):
                    break
                continue
            last_ok = time.time()
            while not self._stop.is_set():
                ok, frame = cap.read()
                now = time.time()
                if not ok or frame is None:
                    if now - last_ok > self._read_timeout:
                        log.warning("Read timeout, reconnecting")
                        break
                    if self._stop.wait(0.02):
                        break
                    continue
                last_ok = now
                if self._min_interval and (now - self._last_frame_ts) < self._min_interval:
                    continue
                self._push(frame, now)
            try:
                cap.release()
            except Exception:
                pass
            if not self._stop.is_set():
                log.info("Reconnecting in %ss", self._reconnect)
                if self._stop.wait(self._reconnect):
                    break
        log.info("VideoDecoder stopped (frames=%d)", self._frame_count)

    def _push(self, frame, ts: float) -> None:
        # Always keep only the latest frame to avoid backlog.
        try:
            self._queue.put_nowait((ts, frame))
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait((ts, frame))
            except queue.Full:
                return
        self._frame_count += 1
        self._last_frame_ts = ts
