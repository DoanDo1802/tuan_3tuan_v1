"""Geometric helpers: point-in-polygon, bbox conversions."""
from __future__ import annotations

from typing import Iterable, Sequence


def point_in_polygon(x: float, y: float, polygon: Sequence[Sequence[float]]) -> bool:
    """Ray casting point-in-polygon. polygon = [[x, y], ...]."""
    n = len(polygon)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i][0], polygon[i][1]
        xj, yj = polygon[j][0], polygon[j][1]
        intersect = ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi
        )
        if intersect:
            inside = not inside
        j = i
    return inside


def polygon_is_normalized(polygon: Sequence[Sequence[float]]) -> bool:
    """Heuristic: treat polygon as normalized (0..1) if every coordinate <= 1.5."""
    for p in polygon:
        if p[0] > 1.5 or p[1] > 1.5:
            return False
    return True


def normalize_polygon(
    polygon: Sequence[Sequence[float]], frame_w: int, frame_h: int
) -> list[list[float]]:
    """Return polygon in normalized (0..1) coordinates regardless of input space."""
    if polygon_is_normalized(polygon):
        return [[float(p[0]), float(p[1])] for p in polygon]
    if frame_w <= 0 or frame_h <= 0:
        return [[float(p[0]), float(p[1])] for p in polygon]
    return [[p[0] / frame_w, p[1] / frame_h] for p in polygon]


def bbox_center(bbox: Sequence[float]) -> tuple[float, float]:
    """bbox = [x1, y1, x2, y2] -> (cx, cy)."""
    return (bbox[0] + bbox[2]) * 0.5, (bbox[1] + bbox[3]) * 0.5


def bbox_xyxy_to_normalized(
    bbox: Sequence[float], frame_w: int, frame_h: int
) -> list[float]:
    """Convert pixel bbox -> normalized 0..1 bbox."""
    if frame_w <= 0 or frame_h <= 0:
        return [float(v) for v in bbox]
    return [
        max(0.0, min(1.0, bbox[0] / frame_w)),
        max(0.0, min(1.0, bbox[1] / frame_h)),
        max(0.0, min(1.0, bbox[2] / frame_w)),
        max(0.0, min(1.0, bbox[3] / frame_h)),
    ]


def any_polygon_contains(
    polygons: Iterable[Sequence[Sequence[float]]], x: float, y: float
) -> bool:
    """Return True if (x, y) lies inside any polygon. Empty list => True (no filter)."""
    polygons = list(polygons)
    if not polygons:
        return True
    return any(point_in_polygon(x, y, poly) for poly in polygons)


def polygon_pixel_bounding_rect(
    polygon_norm: Sequence[Sequence[float]],
    frame_w: int,
    frame_h: int,
    padding: int = 0,
) -> tuple[int, int, int, int]:
    """Compute pixel bounding rect of a normalized polygon, expanded by ``padding``.

    Returns ``(x1, y1, x2, y2)`` clamped to frame bounds. Falls back to the full
    frame when the polygon is empty or invalid.
    """
    if not polygon_norm or frame_w <= 0 or frame_h <= 0:
        return (0, 0, frame_w, frame_h)
    xs = [p[0] * frame_w for p in polygon_norm]
    ys = [p[1] * frame_h for p in polygon_norm]
    x1 = max(0, int(min(xs)) - padding)
    y1 = max(0, int(min(ys)) - padding)
    x2 = min(frame_w, int(max(xs)) + padding)
    y2 = min(frame_h, int(max(ys)) + padding)
    if x2 <= x1 or y2 <= y1:
        return (0, 0, frame_w, frame_h)
    return (x1, y1, x2, y2)
