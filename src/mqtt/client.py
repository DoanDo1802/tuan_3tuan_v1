"""MQTT client factory + connection lifecycle helpers."""
from __future__ import annotations

import logging
import time
from typing import Any

import paho.mqtt.client as mqtt

log = logging.getLogger(__name__)


def build_client(cfg: dict[str, Any], suffix: str) -> mqtt.Client:
    """Create a configured paho client. Use a distinct suffix per client purpose."""
    prefix = cfg.get("client_id_prefix", "ai_engine")
    client_id = f"{prefix}_{suffix}_{int(time.time() * 1000) % 1_000_000}"
    client = mqtt.Client(client_id=client_id, clean_session=True)
    username = cfg.get("username")
    if username:
        client.username_pw_set(username, cfg.get("password"))
    return client


def connect_loop(client: mqtt.Client, cfg: dict[str, Any]) -> None:
    """Blocking connect with retry, then start the background loop."""
    broker = cfg.get("broker")
    port = int(cfg.get("port", 1883))
    keepalive = int(cfg.get("keepalive", 60))
    attempt = 0
    while True:
        try:
            client.connect(broker, port, keepalive)
            client.loop_start()
            log.info("MQTT connected to %s:%s", broker, port)
            return
        except Exception as exc:  # noqa: BLE001
            attempt += 1
            log.warning("MQTT connect failed (attempt %d): %s", attempt, exc)
            time.sleep(min(2 ** attempt, 15))


def stop(client: mqtt.Client) -> None:
    try:
        client.loop_stop()
        client.disconnect()
    except Exception:  # noqa: BLE001
        pass
