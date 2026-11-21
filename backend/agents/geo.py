"""Point-in-polygon checks for the named service areas.

Boundaries are simplified municipal polygons. A search still uses the
rectangular bbox. A point inside that rectangle is kept only when it is
also inside the polygon, so Georgetown, Norval proper, and Trenton do not
become leads just because they sit in the box.
"""

import json
from pathlib import Path


def load_ring(path: Path) -> tuple[tuple[float, float], ...] | None:
    """Return the outer ring as (lon, lat) pairs, or None when the file is missing."""
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    geometry = payload.get("geometry") if isinstance(payload, dict) and payload.get("type") == "Feature" else payload
    if not isinstance(geometry, dict):
        return None
    shape = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if shape == "Polygon" and coordinates:
        ring = coordinates[0]
    elif shape == "MultiPolygon" and coordinates:
        ring = max(coordinates, key=lambda polygon: len(polygon[0]))[0]
    else:
        return None
    points: list[tuple[float, float]] = []
    for pair in ring:
        if not isinstance(pair, (list, tuple)) or len(pair) < 2:
            return None
        lon, lat = float(pair[0]), float(pair[1])
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            return None
        points.append((lon, lat))
    if len(points) < 3:
        return None
    return tuple(points)


def contains(ring: tuple[tuple[float, float], ...] | None, lon: float, lat: float) -> bool:
    """Ray-cast `lon, lat` against one ring. A missing ring does not exclude the point."""
    if not ring:
        return True
    inside = False
    previous_lon, previous_lat = ring[-1]
    for current_lon, current_lat in ring:
        crosses = (current_lat > lat) != (previous_lat > lat)
        if crosses:
            edge_lon = (previous_lon - current_lon) * (lat - current_lat) / (
                (previous_lat - current_lat) or 1e-15
            ) + current_lon
            if lon < edge_lon:
                inside = not inside
        previous_lon, previous_lat = current_lon, current_lat
    return inside


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Approximate metres between two WGS84 points. Accurate at city scale."""
    mean_lat = (lat1 + lat2) / 2
    lat_m = (lat1 - lat2) * 111_320
    lon_m = (lon1 - lon2) * 111_320 * _cos(mean_lat)
    return (lat_m * lat_m + lon_m * lon_m) ** 0.5


def _cos(latitude: float) -> float:
    # Taylor cosine, degrees. Enough for a neighbourhood comparison.
    radians = latitude * 0.017453292519943295
    r2 = radians * radians
    return 1 - r2 / 2 + r2 * r2 / 24
