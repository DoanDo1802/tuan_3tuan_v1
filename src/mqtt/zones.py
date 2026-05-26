"""Zone manager: subscribe smart_vms/cameras/{code}/zones and store polygons."""
from __future__ import annotations

import json
import logging
import threading
from typing import Any

import paho.mqtt.client as mqtt

from ..utils.geometry import any_polygon_contains, normalize_polygon
from . import client as mqtt_client
from .discovery import _normalize_modules

log = logging.getLogger(__name__)


def _point(value: Any) -> list[float] | None:
    try:
        if isinstance(value, dict):
            x = value.get("x", value.get("X"))
            y = value.get("y", value.get("Y"))
            if x is not None and y is not None:
                return [float(x), float(y)]
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            return [float(value[0]), float(value[1])]
    except Exception:
        return None
    return None


def _extract_points(zone: dict[str, Any]) -> list[list[float]] | None:
    for key in ("points", "polygon", "active_area", "area"):
        raw = zone.get(key)
        if isinstance(raw, list):
            pts = [p for p in (_point(item) for item in raw) if p is not None]
            if len(pts) >= 3:
                return pts
        if isinstance(raw, dict):
            for sub_key in ("points", "active_area", "area"):
                sub = raw.get(sub_key)
                if isinstance(sub, list):
                    pts = [p for p in (_point(item) for item in sub) if p is not None]
                    if len(pts) >= 3:
                        return pts
    return None


def _camera_code_from_topic(topic: str) -> str | None:
    parts = topic.split("/")
    if len(parts) >= 2 and parts[-1] == "zones":
        return parts[-2]
    return None


class ZoneManager:
    """Tracks polygon zones for one or more cameras, filtered by ai_module."""

    def __init__(self, mqtt_cfg: dict[str, Any], topic_wildcard: str, ai_module: str):
        self._cfg = mqtt_cfg
        self._topic = topic_wildcard
        self._ai_module = ai_module.upper()
        self._lock = threading.Lock()
        # camera_code -> list of {"zone_id": ..., "polygon": [[x,y], ...]}
        self._zones: dict[str, list[dict[str, Any]]] = {}
        self._client: mqtt.Client | None = None

    def start(self) -> None:
        client = mqtt_client.build_client(self._cfg, "zones")
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        mqtt_client.connect_loop(client, self._cfg)
        self._client = client

    def stop(self) -> None:
        if self._client is not None:
            mqtt_client.stop(self._client)
            self._client = None

    def zones_for(self, camera_code: str) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._zones.get(camera_code, []))

    def polygons_for(self, camera_code: str) -> list[list[list[float]]]:
        return [z["polygon"] for z in self.zones_for(camera_code) if z.get("polygon")]

    def normalized_polygons_for(
        self, camera_code: str, frame_w: int, frame_h: int
    ) -> list[list[list[float]]]:
        return [
            normalize_polygon(poly, frame_w, frame_h)
            for poly in self.polygons_for(camera_code)
        ]

    def point_in_active_zones(
        self,
        camera_code: str,
        x_norm: float,
        y_norm: float,
        frame_w: int,
        frame_h: int,
    ) -> bool:
        """Return True if normalized point lies in any active polygon for the camera.

        If no zone is published yet, return True so detection is not silently dropped
        before the web sends polygons.
        """
        polys = self.normalized_polygons_for(camera_code, frame_w, frame_h)
        return any_polygon_contains(polys, x_norm, y_norm)

    # paho callbacks --------------------------------------------------------
    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            qos = int(self._cfg.get("qos", {}).get("zones", 1))
            client.subscribe(self._topic, qos=qos)
            log.info("Zones subscribed: %s", self._topic)
        else:
            log.error("Zones connect failed rc=%s", rc)

    def _on_message(self, client, userdata, msg):
        camera_code = _camera_code_from_topic(msg.topic)
        if not camera_code:
            return
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception as exc:
            log.warning("Zones JSON parse error: %s", exc)
            return
        zones: list[dict[str, Any]] = []
        for zone in payload.get("zones") or []:
            if not isinstance(zone, dict) or not zone.get("is_active", True):
                continue
            modules = _normalize_modules(zone.get("ai_modules") or zone.get("aiModules"))
            if modules and self._ai_module not in modules:
                continue
            pts = _extract_points(zone)
            if pts:
                zones.append({
                    "zone_id": zone.get("id") or zone.get("zone_id"),
                    "polygon": pts,
                })
        with self._lock:
            if zones:
                self._zones[camera_code] = zones
            else:
                self._zones.pop(camera_code, None)
        log.info("Zones updated camera=%s count=%d", camera_code, len(zones))
