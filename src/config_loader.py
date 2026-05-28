"""YAML config loader with topic templating."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Config:
    raw: dict[str, Any] = field(default_factory=dict)

    # Convenience accessors -------------------------------------------------
    @property
    def mqtt(self) -> dict[str, Any]:
        return self.raw.get("mqtt", {})

    @property
    def company_id(self) -> int:
        return int(self.raw.get("company", {}).get("id", 0))

    @property
    def ai_module(self) -> str:
        return str(self.raw.get("company", {}).get("ai_module", "")).upper()

    @property
    def camera_code(self) -> str:
        return str(self.raw.get("camera", {}).get("code", "")).strip()

    @property
    def rtsp_override(self) -> str:
        return str(self.raw.get("camera", {}).get("rtsp_override", "")).strip()

    @property
    def topics(self) -> dict[str, str]:
        return self.raw.get("topics", {})

    @property
    def models(self) -> dict[str, Any]:
        return self.raw.get("models", {})

    @property
    def tracker(self) -> dict[str, Any]:
        return self.raw.get("tracker", {})

    @property
    def ocr(self) -> dict[str, Any]:
        return self.raw.get("ocr", {})

    @property
    def video(self) -> dict[str, Any]:
        return self.raw.get("video", {})

    @property
    def publish(self) -> dict[str, Any]:
        return self.raw.get("publish", {})

    @property
    def zone_filter(self) -> dict[str, Any]:
        return self.raw.get("zone_filter", {})

    @property
    def logging(self) -> dict[str, Any]:
        return self.raw.get("logging", {})

    # Topic rendering -------------------------------------------------------
    def cameras_topic(self) -> str:
        return self.topics.get("cameras", "").format(company_id=self.company_id)

    def zones_topic(self, camera_code: str | None = None) -> str:
        code = camera_code if camera_code is not None else self.camera_code
        return self.topics.get("zones", "").format(camera_code=code)

    def zones_topic_wildcard(self) -> str:
        return self.topics.get("zones", "").format(camera_code="+")

    def bbox_topic(self, camera_code: str | None = None) -> str:
        code = camera_code if camera_code is not None else self.camera_code
        return self.topics.get("bbox", "").format(camera_code=code)


def load_dotenv(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"\''))


def load_config(path: str) -> Config:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Config file not found: {path}")
    load_dotenv()
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(os.path.expandvars(f.read())) or {}
    return Config(raw=raw)
