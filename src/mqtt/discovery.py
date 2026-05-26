"""Camera discovery: listen to smart_vms/cameras/company/{id}."""
from __future__ import annotations

import json
import logging
import threading
from typing import Any

import paho.mqtt.client as mqtt

from . import client as mqtt_client

log = logging.getLogger(__name__)


def _normalize_modules(value: Any) -> list[str]:
    if isinstance(value, list):
        items = value
    elif isinstance(value, str):
        s = value.strip()
        if s.startswith("["):
            try:
                items = json.loads(s)
            except Exception:
                items = [s]
        else:
            items = [s]
    elif value is None:
        items = []
    else:
        items = [value]
    return [str(x).strip().upper() for x in items if str(x).strip()]


def _parse_json_value(value: Any, default: Any) -> Any:
    if isinstance(value, str):
        s = value.strip()
        if s.startswith("{") or s.startswith("["):
            try:
                return json.loads(s)
            except Exception:
                return default
    return value if value is not None else default


def _select_rtsp(cam: dict[str, Any], ai_module: str) -> str:
    restream = _parse_json_value(cam.get("restream_urls") or cam.get("restreamUrls"), {})
    if isinstance(restream, dict):
        for key in (ai_module, ai_module.lower(), ai_module.upper()):
            v = restream.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    if isinstance(restream, list):
        for v in restream:
            if isinstance(v, str) and v.strip():
                return v.strip()
    for key in ("stream_url", "streamUrl", "rtsp", "url", "link"):
        v = cam.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


class CameraDiscovery:
    """Subscribe to the cameras topic and keep the latest camera list."""

    def __init__(self, mqtt_cfg: dict[str, Any], topic: str, ai_module: str):
        self._cfg = mqtt_cfg
        self._topic = topic
        self._ai_module = ai_module.upper()
        self._lock = threading.Lock()
        self._cameras: list[dict[str, Any]] = []
        self._received = threading.Event()
        self._client: mqtt.Client | None = None

    def start(self) -> None:
        client = mqtt_client.build_client(self._cfg, "discovery")
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        mqtt_client.connect_loop(client, self._cfg)
        self._client = client

    def stop(self) -> None:
        if self._client is not None:
            mqtt_client.stop(self._client)
            self._client = None

    def wait_first(self, timeout: float = 10.0) -> bool:
        return self._received.wait(timeout=timeout)

    def cameras(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._cameras)

    def find_by_code(self, code: str) -> dict[str, Any] | None:
        code = (code or "").strip()
        for cam in self.cameras():
            if str(cam.get("code") or "").strip() == code:
                return cam
        return None

    # paho callbacks --------------------------------------------------------
    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            qos = int(self._cfg.get("qos", {}).get("cameras", 1))
            client.subscribe(self._topic, qos=qos)
            log.info("Discovery subscribed: %s", self._topic)
        else:
            log.error("Discovery connect failed rc=%s", rc)

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception as exc:
            log.warning("Discovery JSON parse error: %s", exc)
            return
        cams: list[dict[str, Any]] = []
        for cam in payload.get("cameras") or []:
            if not isinstance(cam, dict):
                continue
            modules = _normalize_modules(cam.get("ai_modules") or cam.get("aiModules"))
            status = str(cam.get("status") or "").upper()
            code = str(cam.get("code") or cam.get("name") or cam.get("id") or "").strip()
            rtsp = _select_rtsp(cam, self._ai_module)
            if code and rtsp and self._ai_module in modules and status == "ONLINE":
                cams.append({
                    "id": cam.get("id"),
                    "code": code,
                    "name": cam.get("name"),
                    "rtsp": rtsp,
                    "ai_modules": modules,
                })
        with self._lock:
            self._cameras = cams
        log.info("Discovery: %d ONLINE camera(s) for module %s", len(cams), self._ai_module)
        self._received.set()
