"""MQTT bbox publisher thread (Thread 4)."""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
from typing import Any

from . import client as mqtt_client

log = logging.getLogger(__name__)


class BboxPublisher(threading.Thread):
    """Reads detection payloads from a queue and publishes them as JSON."""

    def __init__(
        self,
        mqtt_cfg: dict[str, Any],
        topic: str,
        publish_queue: "queue.Queue[dict[str, Any]]",
        stop_event: threading.Event,
        publish_interval: float = 0.04,
    ):
        super().__init__(name="BboxPublisher", daemon=True)
        self._cfg = mqtt_cfg
        self._topic = topic
        self._queue = publish_queue
        # Tránh shadow Thread._stop() (private method của threading.Thread).
        self._stop_event = stop_event
        self._interval = max(0.0, float(publish_interval))
        self._qos = int(mqtt_cfg.get("qos", {}).get("bbox", 0))
        self._client = None
        self._last_publish_ts = 0.0
        self._published = 0

    @property
    def published_count(self) -> int:
        return self._published

    def run(self) -> None:
        self._client = mqtt_client.build_client(self._cfg, "publisher")
        mqtt_client.connect_loop(self._client, self._cfg)
        log.info("Publisher ready on topic=%s qos=%s", self._topic, self._qos)
        try:
            while not self._stop_event.is_set():
                try:
                    payload = self._queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                # Drain backlog → publish payload mới nhất, bỏ payload cũ
                # để tránh hiển thị bbox của frame quá khứ.
                while True:
                    try:
                        payload = self._queue.get_nowait()
                    except queue.Empty:
                        break
                now = time.time()
                wait = self._interval - (now - self._last_publish_ts)
                if wait > 0:
                    if self._stop_event.wait(wait):
                        break
                self._publish(payload)
                self._last_publish_ts = time.time()
        finally:
            if self._client is not None:
                mqtt_client.stop(self._client)
                self._client = None
            log.info("Publisher stopped (published=%d)", self._published)

    def _publish(self, payload: dict[str, Any]) -> None:
        try:
            data = json.dumps(payload, ensure_ascii=False)
        except Exception as exc:
            log.warning("Publisher JSON encode error: %s", exc)
            return
        try:
            info = self._client.publish(self._topic, data, qos=self._qos)
            if self._qos > 0:
                info.wait_for_publish(timeout=2.0)
            self._published += 1
        except Exception as exc:
            log.warning("Publisher publish error: %s", exc)
