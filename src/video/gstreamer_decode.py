"""RTSP decode thread using native GStreamer via PyGObject (no OpenCV)."""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any

import gi
import numpy as np

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

log = logging.getLogger(__name__)

_GST_INIT_LOCK = threading.Lock()
_GST_INITIALISED = False


def _ensure_gst_init() -> None:
    global _GST_INITIALISED
    with _GST_INIT_LOCK:
        if not _GST_INITIALISED:
            Gst.init(None)
            _GST_INITIALISED = True


def _build_pipeline_str(template: str, url: str) -> str:
    pipe = template.format(url=url)
    pipe = " ".join(pipe.split())
    if "appsink" in pipe and "name=" not in pipe.split("appsink", 1)[1].split("!")[0]:
        pipe = pipe.replace("appsink", "appsink name=sink", 1)
    return pipe


def _sample_to_ndarray(sample):
    buf = sample.get_buffer()
    caps = sample.get_caps()
    if buf is None or caps is None:
        return None
    structure = caps.get_structure(0)
    ok_w, width = structure.get_int("width")
    ok_h, height = structure.get_int("height")
    if not (ok_w and ok_h):
        return None
    success, map_info = buf.map(Gst.MapFlags.READ)
    if not success:
        return None
    try:
        data_size = map_info.size
        tight = width * height * 3
        if data_size == tight:
            # No stride padding — fast path.
            frame = np.ndarray(
                shape=(height, width, 3),
                dtype=np.uint8,
                buffer=map_info.data,
            ).copy()
        else:
            # Buffer is row-padded. Stride = data_size / height, strip the padding.
            if data_size % height != 0:
                return None
            stride = data_size // height
            if stride < width * 3:
                return None
            padded = np.ndarray(
                shape=(height, stride),
                dtype=np.uint8,
                buffer=map_info.data,
            )
            frame = padded[:, : width * 3].reshape(height, width, 3).copy()
    finally:
        buf.unmap(map_info)
    return frame


class VideoDecoder(threading.Thread):
    """Thread 1: pull frames from a GStreamer pipeline and push to a queue."""

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
        # NOTE: phải đặt tên KHÁC `_stop` vì Thread có private method Thread._stop()
        # — nếu shadow sẽ break Thread.is_alive()/join() ở Python 3.11+.
        self._stop_event = stop_event
        self._frame_count = 0
        self._last_frame_ts = 0.0
        self._target_fps = float(video_cfg.get("target_fps", 0) or 0)
        self._min_interval = 1.0 / self._target_fps if self._target_fps > 0 else 0.0
        self._read_timeout = float(video_cfg.get("read_timeout_seconds", 5.0))
        self._reconnect = float(video_cfg.get("reconnect_seconds", 3.0))
        _ensure_gst_init()

    @property
    def frame_count(self) -> int:
        return self._frame_count

    def _open_pipeline(self):
        pipe_str = _build_pipeline_str(self._cfg.get("pipeline_template", ""), self._url)
        log.info("Launching GStreamer pipeline: %s", pipe_str)
        try:
            pipeline = Gst.parse_launch(pipe_str)
        except Exception as exc:
            log.error("Failed to parse GStreamer pipeline: %s", exc)
            return None
        sink = pipeline.get_by_name("sink")
        if sink is None:
            log.error("appsink 'sink' not found in pipeline")
            pipeline.set_state(Gst.State.NULL)
            return None
        ret = pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            log.error("Failed to set pipeline to PLAYING")
            pipeline.set_state(Gst.State.NULL)
            return None
        return pipeline, sink

    def _drain_bus(self, pipeline) -> bool:
        bus = pipeline.get_bus()
        msg = bus.pop_filtered(Gst.MessageType.ERROR | Gst.MessageType.EOS)
        if msg is None:
            return True
        if msg.type == Gst.MessageType.ERROR:
            err, debug = msg.parse_error()
            log.warning("GStreamer error: %s (%s)", err, debug)
        else:
            log.info("GStreamer EOS")
        return False

    def run(self) -> None:
        log.info("VideoDecoder starting url=%s", self._url)
        timeout_ns = int(0.2 * Gst.SECOND)
        while not self._stop_event.is_set():
            opened = self._open_pipeline()
            if opened is None:
                if self._stop_event.wait(self._reconnect):
                    break
                continue
            pipeline, sink = opened
            last_ok = time.time()
            while not self._stop_event.is_set():
                if not self._drain_bus(pipeline):
                    break
                sample = sink.emit("try-pull-sample", timeout_ns)
                now = time.time()
                if sample is None:
                    if now - last_ok > self._read_timeout:
                        log.warning("Read timeout, reconnecting")
                        break
                    continue
                frame = _sample_to_ndarray(sample)
                if frame is None:
                    continue
                last_ok = now
                if self._min_interval and (now - self._last_frame_ts) < self._min_interval:
                    continue
                self._push(frame, now)
            pipeline.set_state(Gst.State.NULL)
            if not self._stop_event.is_set():
                log.info("Reconnecting in %ss", self._reconnect)
                if self._stop_event.wait(self._reconnect):
                    break
        log.info("VideoDecoder stopped (frames=%d)", self._frame_count)

    def _push(self, frame, ts: float) -> None:
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