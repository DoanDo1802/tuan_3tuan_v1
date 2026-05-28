"""Standalone smoke test cho VideoDecoder. Chạy:
    python scripts/test_gst_decode.py rtsp://USER:PASS@HOST/Streaming/Channels/101
"""
from __future__ import annotations

import logging
import queue
import sys
import threading
import time

from src.video.gstreamer_decode import VideoDecoder

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)
log = logging.getLogger("test_gst_decode")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/test_gst_decode.py <rtsp_url>")
        sys.exit(2)
    url = sys.argv[1]

    cfg = {
        "use_gstreamer": True,
        "pipeline_template": (
            "rtspsrc location={url} latency=0 drop-on-latency=true ! "
            "rtph264depay ! h264parse ! avdec_h264 ! "
            "videoconvert ! video/x-raw,format=BGR ! "
            "appsink sync=false max-buffers=1 drop=true"
        ),
        "reconnect_seconds": 3,
        "read_timeout_seconds": 5,
        "target_fps": 0,
    }

    fq: queue.Queue = queue.Queue(maxsize=4)
    stop = threading.Event()
    dec = VideoDecoder(url, cfg, fq, stop)
    dec.start()

    deadline = time.time() + 15
    received = 0
    try:
        while time.time() < deadline and received < 10:
            try:
                ts, frame = fq.get(timeout=1.0)
            except queue.Empty:
                log.warning("No frame yet...")
                continue
            received += 1
            log.info(
                "frame #%d ts=%.3f shape=%s dtype=%s",
                received,
                ts,
                frame.shape,
                frame.dtype,
            )
    finally:
        stop.set()
        dec.join(timeout=3)

    if received == 0:
        log.error("FAIL: no frames received")
        sys.exit(1)
    log.info("OK: received %d frames", received)


if __name__ == "__main__":
    main()
