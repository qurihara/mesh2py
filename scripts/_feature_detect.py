"""segment grouping + 2D feature (circle/rect/polygon) detection."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from shapely.geometry import Polygon, MultiPolygon
from shapely.geometry.polygon import orient

from _mesh_utils import Slice2D


@dataclass
class Feature2D:
    kind: str               # "circle" | "rectangle" | "polygon"
    centroid: tuple[float, float]
    # circle
    radius: float | None = None
    # rectangle
    width: float | None = None
    height: float | None = None
    rotation_deg: float | None = None  # 0 if axis-aligned
    # polygon
    points: list[tuple[float, float]] | None = None
    # holes (each entry is a Feature2D)
    holes: list["Feature2D"] = field(default_factory=list)


@dataclass
class Segment:
    z_lo: float
    z_hi: float
    rep_features: list[Feature2D]   # one per disjoint outer ring on the slice


# ---------------------------------------------------------------- ring detect

def _classify_ring(coords: list[tuple[float, float]], tol_rel: float = 0.04) -> Feature2D:
    """coords: closed ring (last==first or not). Returns Feature2D (no holes)."""
    pts = np.array(coords, dtype=float)
    if np.allclose(pts[0], pts[-1]):
        pts = pts[:-1]
    poly = Polygon(pts)
    if not poly.is_valid or poly.area <= 0:
        poly = poly.buffer(0)
    area = poly.area
    cx, cy = poly.centroid.x, poly.centroid.y

    # ---- circle test
    dists = np.linalg.norm(pts - np.array([cx, cy]), axis=1)
    r_mean = float(dists.mean())
    r_std = float(dists.std())
    r_area = float(np.sqrt(area / np.pi))
    if r_mean > 1e-6 and r_std / r_mean < tol_rel and abs(r_mean - r_area) / r_mean < tol_rel:
        return Feature2D(kind="circle", centroid=(cx, cy), radius=r_mean)

    # ---- rectangle test (min-area rotated bbox)
    rect = poly.minimum_rotated_rectangle
    if rect.area > 0 and abs(rect.area - area) / rect.area < tol_rel:
        rcoords = list(rect.exterior.coords)[:-1]
        edges = [
            (np.array(rcoords[(i + 1) % 4]) - np.array(rcoords[i])) for i in range(4)
        ]
        lens = [float(np.linalg.norm(e)) for e in edges]
        w = (lens[0] + lens[2]) / 2.0
        h = (lens[1] + lens[3]) / 2.0
        # rotation from longest edge
        long_idx = int(np.argmax([lens[0], lens[1]]))
        e = edges[long_idx]
        rot = float(np.degrees(np.arctan2(e[1], e[0])))
        # normalize to (-90, 90]
        while rot > 90:
            rot -= 180
        while rot <= -90:
            rot += 180
        # ensure width >= height
        if w < h:
            w, h = h, w
        return Feature2D(
            kind="rectangle",
            centroid=(cx, cy),
            width=w,
            height=h,
            rotation_deg=rot if abs(rot) > 0.5 else 0.0,
        )

    # ---- fallback: polygon
    return Feature2D(
        kind="polygon",
        centroid=(cx, cy),
        points=[(float(x), float(y)) for x, y in pts.tolist()],
    )


def polygons_to_features(polys: list[Polygon]) -> list[Feature2D]:
    feats: list[Feature2D] = []
    for p in polys:
        if isinstance(p, MultiPolygon):
            geoms = list(p.geoms)
        else:
            geoms = [p]
        for g in geoms:
            g = orient(g, sign=1.0)  # CCW exterior
            ext = list(g.exterior.coords)
            f = _classify_ring(ext)
            for ring in g.interiors:
                hole = _classify_ring(list(ring.coords))
                f.holes.append(hole)
            feats.append(f)
    return feats


# ---------------------------------------------------------------- segmenting

def _match_polygons(a: list[Polygon], b: list[Polygon]) -> list[tuple[Polygon, Polygon]] | None:
    """Greedy match by centroid distance. Returns None if counts differ."""
    if len(a) != len(b):
        return None
    remaining = list(range(len(b)))
    pairs: list[tuple[Polygon, Polygon]] = []
    for pa in a:
        cx, cy = pa.centroid.x, pa.centroid.y
        best_j = min(remaining, key=lambda j: (b[j].centroid.x - cx) ** 2 + (b[j].centroid.y - cy) ** 2)
        pairs.append((pa, b[best_j]))
        remaining.remove(best_j)
    return pairs


def _slices_similar(seed: list[Polygon], cur: list[Polygon], tol: float) -> bool:
    """All polygons match 1:1 within Hausdorff `tol`."""
    pairs = _match_polygons(seed, cur)
    if pairs is None:
        return False
    for pa, pb in pairs:
        if pa.hausdorff_distance(pb) > tol:
            return False
    return True


def segment_slices(
    slices: list[Slice2D],
    step: float,
    merge_tol: float | None = None,
) -> list[Segment]:
    """Group consecutive slices whose polygons stay within Hausdorff `merge_tol`
    of the segment's seed slice."""
    if not slices:
        return []
    if merge_tol is None:
        merge_tol = max(step * 1.5, 0.4)

    segments: list[Segment] = []
    seed_idx = 0
    for i in range(1, len(slices)):
        if not _slices_similar(slices[seed_idx].polygons, slices[i].polygons, merge_tol):
            z_lo = slices[seed_idx].z - step / 2
            z_hi = slices[i - 1].z + step / 2
            feats = polygons_to_features(slices[seed_idx].polygons)
            segments.append(Segment(z_lo=float(z_lo), z_hi=float(z_hi), rep_features=feats))
            seed_idx = i
    z_lo = slices[seed_idx].z - step / 2
    z_hi = slices[-1].z + step / 2
    feats = polygons_to_features(slices[seed_idx].polygons)
    segments.append(Segment(z_lo=float(z_lo), z_hi=float(z_hi), rep_features=feats))

    # clamp to non-negative Z (mesh was shifted to origin)
    for s in segments:
        if s.z_lo < 0:
            s.z_lo = 0.0
    return segments
