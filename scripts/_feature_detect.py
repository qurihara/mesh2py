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


@dataclass
class LoftGroup:
    """A run of consecutive segments with matching topology, to be emitted
    as a single `loft(...)` instead of N stacked extrudes.

    `segments` keeps the original per-slice segments so the generated
    script can fall back to a stack of extrudes if OCCT loft fails at
    runtime."""
    z_lo: float
    z_hi: float
    features_lo: list[Feature2D]
    features_hi: list[Feature2D]
    segments: list[Segment]
    n_merged: int = 1   # how many original segments were collapsed into this


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


def polygons_to_features(polys: list[Polygon],
                          *, drop_hole_area: float = 0.0,
                          drop_outer_area: float = 0.0) -> list[Feature2D]:
    """Convert a list of shapely polygons into Feature2D objects.

    - `drop_hole_area`: interior rings (holes) whose absolute area is below
      this threshold are discarded. Useful to ignore engraved label text
      and other fine detail that hurts loft/segment compression without
      meaningfully affecting the printed shape.
    - `drop_outer_area`: standalone outer polygons below this area are
      dropped entirely (engraved letter islands).
    """
    feats: list[Feature2D] = []
    for p in polys:
        if isinstance(p, MultiPolygon):
            geoms = list(p.geoms)
        else:
            geoms = [p]
        for g in geoms:
            if drop_outer_area > 0 and g.area < drop_outer_area:
                continue
            g = orient(g, sign=1.0)  # CCW exterior
            ext = list(g.exterior.coords)
            f = _classify_ring(ext)
            for ring in g.interiors:
                hole_poly = Polygon(list(ring.coords))
                if drop_hole_area > 0 and hole_poly.area < drop_hole_area:
                    continue
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
    *,
    drop_hole_area: float = 0.0,
    drop_outer_area: float = 0.0,
) -> list[Segment]:
    """Group consecutive slices whose polygons stay within Hausdorff `merge_tol`
    of the segment's seed slice."""
    if not slices:
        return []
    if merge_tol is None:
        merge_tol = max(step * 1.5, 0.4)

    def _feats(polys):
        return polygons_to_features(
            polys, drop_hole_area=drop_hole_area, drop_outer_area=drop_outer_area,
        )

    segments: list[Segment] = []
    seed_idx = 0
    for i in range(1, len(slices)):
        if not _slices_similar(slices[seed_idx].polygons, slices[i].polygons, merge_tol):
            z_lo = slices[seed_idx].z - step / 2
            z_hi = slices[i - 1].z + step / 2
            segments.append(Segment(z_lo=float(z_lo), z_hi=float(z_hi),
                                    rep_features=_feats(slices[seed_idx].polygons)))
            seed_idx = i
    z_lo = slices[seed_idx].z - step / 2
    z_hi = slices[-1].z + step / 2
    segments.append(Segment(z_lo=float(z_lo), z_hi=float(z_hi),
                            rep_features=_feats(slices[seed_idx].polygons)))

    # clamp to non-negative Z (mesh was shifted to origin)
    for s in segments:
        if s.z_lo < 0:
            s.z_lo = 0.0
    return segments


# ---------------------------------------------------------------- loft grouping

def _features_match_topology(a: list[Feature2D], b: list[Feature2D]) -> bool:
    """True iff a and b can be lofted between (same count + per-feature kind
    + same number of vertices for polygons + same number of holes)."""
    if len(a) != len(b):
        return False
    # pair features by centroid distance
    used = set()
    pairs = []
    for fa in a:
        best_j = None
        best_d = float("inf")
        for j, fb in enumerate(b):
            if j in used:
                continue
            dx = fa.centroid[0] - fb.centroid[0]
            dy = fa.centroid[1] - fb.centroid[1]
            d = dx * dx + dy * dy
            if d < best_d:
                best_d = d
                best_j = j
        if best_j is None:
            return False
        used.add(best_j)
        pairs.append((fa, b[best_j]))
    # check per-feature kind + topology
    for fa, fb in pairs:
        if fa.kind != fb.kind:
            return False
        if fa.kind == "polygon":
            if len(fa.points or []) != len(fb.points or []):
                return False
        if len(fa.holes) != len(fb.holes):
            return False
        for ha, hb in zip(fa.holes, fb.holes):
            if ha.kind != hb.kind:
                return False
            if ha.kind == "polygon" and len(ha.points or []) != len(hb.points or []):
                return False
    return True


Group = Segment | LoftGroup


def _loft_safe(features: list[Feature2D], *, max_poly_pts: int = 80) -> bool:
    """Heuristic: OCCT make_loft can usually handle multiple disjoint
    sections as long as they pair up by topology. We allow up to 4
    features per section, no holes, and polygons under a vertex cap.
    The generated script wraps each loft in try/except so OCCT failures
    fall back to extrude at runtime."""
    if not features:
        return False
    if len(features) > 4:
        return False
    for f in features:
        if f.holes:
            return False
        if f.kind == "polygon" and len(f.points or []) > max_poly_pts:
            return False
    return True


def group_lofts(segments: list[Segment]) -> list[Group]:
    """Collapse runs of consecutive segments with matching topology into
    LoftGroups (one loft op instead of N extrudes). Useful for tapered/
    swept surfaces that the slicer breaks into many tiny segments.

    Only single-feature, hole-free runs are lofted to keep OCCT happy."""
    if not segments:
        return []
    out: list[Group] = []
    i = 0
    while i < len(segments):
        # Lookahead: matching topology, AND each section loft-safe.
        j = i + 1
        while (j < len(segments)
               and _loft_safe(segments[i].rep_features)
               and _loft_safe(segments[j].rep_features)
               and _features_match_topology(
                   segments[j - 1].rep_features, segments[j].rep_features
               )):
            j += 1
        if j - i >= 2 and _loft_safe(segments[i].rep_features):
            out.append(LoftGroup(
                z_lo=segments[i].z_lo,
                z_hi=segments[j - 1].z_hi,
                features_lo=segments[i].rep_features,
                features_hi=segments[j - 1].rep_features,
                segments=list(segments[i:j]),
                n_merged=j - i,
            ))
        else:
            out.append(segments[i])
            j = i + 1
        i = j
    return out
