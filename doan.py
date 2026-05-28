#!/usr/bin/env python3
"""Entrypoint for the AI Vehicle & License Plate Recognition Engine."""
from __future__ import annotations

import argparse
import logging
import sys

from src.config_loader import load_config
from src.pipeline import Pipeline


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI Engine — Vehicle & Plate Recognition")
    parser.add_argument(
        "--config",
        default="config/settings.yaml",
        help="Path to YAML config (default: config/settings.yaml)",
    )
    parser.add_argument(
        "--camera-code",
        default="",
        help="Override camera.code from config (must be unique on the shared broker).",
    )
    parser.add_argument(
        "--rtsp",
        default="",
        help="Override RTSP URL (skip MQTT discovery for camera URL).",
    )
    return parser.parse_args()


def setup_logging(cfg) -> None:
    level = str(cfg.logging.get("level", "INFO")).upper()
    fmt = cfg.logging.get("fmt", "%(asctime)s %(levelname)s %(name)s :: %(message)s")
    logging.basicConfig(level=level, format=fmt)


def main() -> int:
    args = parse_args()
    cfg = load_config(args.config)
    if args.camera_code.strip():
        cfg.raw.setdefault("camera", {})["code"] = args.camera_code.strip()
    if args.rtsp.strip():
        cfg.raw.setdefault("camera", {})["rtsp_override"] = args.rtsp.strip()
    setup_logging(cfg)
    log = logging.getLogger("main")
    if not cfg.camera_code:
        log.error("camera.code is empty — set it in config or pass --camera-code")
        return 2
    log.info(
        "Starting AI engine company=%s module=%s camera_code=%s",
        cfg.company_id,
        cfg.ai_module,
        cfg.camera_code,
    )
    pipeline = Pipeline(cfg)
    try:
        pipeline.run()
    except Exception as exc:
        log.exception("Pipeline crashed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
