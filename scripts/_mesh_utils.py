"""mesh load / PCA / slice / contour helpers."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import trimesh
from shapely.geometry import LineString, Polygon, MultiPolygon
from shapely.ops import polygonize, unary_union


@dataclass
class Slice2D:
    z: float
    polygons: list[Polygon]


def load_mesh(path: Path) -> trimesh.Trimesh:
    obj = trimesh.load(str(path), force="mesh")
    if isinstance(obj, trimesh.Scene):
        obj = trimesh.util.concatenate(tuple(obj.dump()))
    if not isinstance(obj, trimesh.Trimesh):
        raise TypeError(f"unsupported mesh type: {type(obj)}")
    return obj


def align_to_z(mesh: trimesh.Trimesh, axis: str | None = None,
               *, swap_ratio: float = 0.9) -> trimesh.Trimesh:
    """Rotate so the minimum-variance axis is Z (the "thickness" axis),
    then translate so min corner is at origin.

    Only swap axes when the candidate axis is meaningfully smaller than
    the largest (min/max < `swap_ratio`). For roughly cube-shaped or
    cylindrical (h ≈ 2r) meshes that lack a clear "thin" axis, leave
    the orientation alone — swapping there can lay a cylinder on its
    side and break primitive detection.
    """
    m = mesh.copy()
    if axis in {"x", "y", "z"}:
        idx = {"x": 0, "y": 1, "z": 2}[axis]
        do_swap = (idx != 2)
    else:
        ext = m.extents
        idx = int(np.argmin(ext))
        ratio = float(ext[idx]) / max(float(ext.max()), 1e-9)
        do_swap = (idx != 2) and (ratio < swap_ratio)
    if do_swap and idx != 2:
        # swap idx <-> 2 via permutation matrix
        perm = [0, 1, 2]
        perm[idx], perm[2] = perm[2], perm[idx]
        R = np.eye(4)
        R[:3, :3] = np.eye(3)[perm]
        m.apply_transform(R)
    # translate to origin
    T = np.eye(4)
    T[:3, 3] = -m.bounds[0]
    m.apply_transform(T)
    return m


def _segments_to_polygons(segs_2d: np.ndarray) -> list[Polygon]:
    """Build closed polygons (with holes) from a (N, 2, 2) array of XY line segments."""
    if segs_2d is None or len(segs_2d) == 0:
        return []
    lines = [LineString([(s[0, 0], s[0, 1]), (s[1, 0], s[1, 1])]) for s in segs_2d]
    polys = list(polygonize(lines))
    if not polys:
        return []
    # nest holes: a polygon contained in another's interior becomes a hole.
    polys.sort(key=lambda p: p.area, reverse=True)
    used = [False] * len(polys)
    out: list[Polygon] = []
    for i, outer in enumerate(polys):
        if used[i]:
            continue
        used[i] = True
        holes = []
        # find polys fully inside `outer` and not already claimed
        for j in range(i + 1, len(polys)):
            if used[j]:
                continue
            if outer.contains(polys[j]):
                # is it directly contained (not contained in any other un-used poly)?
                directly = True
                for k in range(i + 1, j):
                    if not used[k] and polys[k].contains(polys[j]):
                        directly = False
                        break
                if directly:
                    holes.append(list(polys[j].exterior.coords))
                    used[j] = True
        out.append(Polygon(list(outer.exterior.coords), holes=holes))
    # filter degenerate
    return [p for p in out if not p.is_empty and p.area > 1e-6]


def slice_z(mesh: trimesh.Trimesh, step: float) -> list[Slice2D]:
    """Horizontal slices in WORLD XY coordinates.

    Uses `mesh.section(...).to_planar(to_2D=identity)` so that the returned
    2D polygons keep their world XY values (no in-plane re-centering).
    """
    z_min, z_max = float(mesh.bounds[0, 2]), float(mesh.bounds[1, 2])
    eps = step * 0.5
    heights = np.arange(z_min + eps, z_max - eps + step * 0.01, step)
    identity = np.eye(4)
    out: list[Slice2D] = []
    for z in heights:
        sec = mesh.section(plane_origin=[0, 0, float(z)], plane_normal=[0, 0, 1])
        if sec is None:
            continue
        planar, _ = sec.to_planar(to_2D=identity)
        polys: list[Polygon] = []
        for poly in planar.polygons_full:
            if poly.is_empty or poly.area < 1e-6:
                continue
            polys.append(poly)
        if polys:
            out.append(Slice2D(z=float(z), polygons=polys))
    return out


def _resample_ring(coords: list[tuple[float, float]], n: int) -> list[tuple[float, float]]:
    """Resample a closed ring to exactly n points evenly spaced along arc length.
    This is critical for the loft compressor: adjacent slices must have
    matching vertex counts AND corresponding points for OCCT to build
    a clean ruled surface between them."""
    pts = np.array(coords, dtype=float)
    if np.allclose(pts[0], pts[-1]):
        pts = pts[:-1]
    if len(pts) < 3 or n < 3:
        return [(float(x), float(y)) for x, y in pts.tolist()]
    # cumulative arc lengths around the closed loop
    closed = np.vstack([pts, pts[0]])
    seg = np.linalg.norm(np.diff(closed, axis=0), axis=1)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    total = float(cum[-1])
    if total <= 0:
        return [(float(x), float(y)) for x, y in pts.tolist()]
    # find an anchor: rightmost point (max-x then max-y), so phase is stable
    anchor = int(np.lexsort((pts[:, 1], pts[:, 0]))[-1])
    anchor_s = float(cum[anchor])
    targets = (anchor_s + np.linspace(0.0, total, n, endpoint=False)) % total
    # interpolate
    xs = np.interp(targets, cum, closed[:, 0])
    ys = np.interp(targets, cum, closed[:, 1])
    return [(float(x), float(y)) for x, y in zip(xs, ys)]


def resample_polys(polys: list[Polygon], n: int) -> list[Polygon]:
    """Resample exterior and interior rings of each polygon to n points
    by arc length. Holes are also resampled to keep their topology stable."""
    if n < 3:
        return polys
    out: list[Polygon] = []
    for p in polys:
        if isinstance(p, MultiPolygon):
            geoms = list(p.geoms)
        else:
            geoms = [p]
        for g in geoms:
            ext = _resample_ring(list(g.exterior.coords), n)
            ints = [_resample_ring(list(r.coords), n) for r in g.interiors]
            out.append(Polygon(ext, holes=ints))
    return out


def strip_small_holes(polys: list[Polygon], min_hole_area: float) -> list[Polygon]:
    """Return polygons with all interior rings (holes) whose area is below
    `min_hole_area` filled in. Useful to ignore engraved label text and
    other fine details that hurt segmentation/loft compression without
    meaningfully affecting the reconstructed silhouette."""
    if min_hole_area <= 0:
        return polys
    out: list[Polygon] = []
    for p in polys:
        if isinstance(p, MultiPolygon):
            geoms = list(p.geoms)
        else:
            geoms = [p]
        for g in geoms:
            kept_interiors = []
            for ring in g.interiors:
                ring_poly = Polygon(list(ring.coords))
                if ring_poly.area >= min_hole_area:
                    kept_interiors.append(list(ring.coords))
            out.append(Polygon(list(g.exterior.coords), holes=kept_interiors))
    return out


def simplify_polys(polys: list[Polygon], tol: float) -> list[Polygon]:
    out: list[Polygon] = []
    for p in polys:
        sp = p.simplify(tol, preserve_topology=True)
        if not sp.is_empty and sp.area > 1e-6:
            if isinstance(sp, MultiPolygon):
                out.extend(list(sp.geoms))
            else:
                out.append(sp)
    return out


def split_components(mesh: trimesh.Trimesh, *, min_volume: float = 50.0,
                     min_faces: int = 50,
                     only_watertight: bool = False) -> list[trimesh.Trimesh]:
    """Split a mesh into connected-component sub-meshes.

    Tinkercad designs typically place several discrete parts on the same
    workplane. Reconstructing each component independently keeps per-part
    segment counts manageable and lets build123d emit one BuildPart per
    component, which is dramatically faster than one BuildPart with
    hundreds of stacked sketches.

    Components below `min_volume` mm^3 OR with fewer than `min_faces`
    triangles are discarded as noise. Tinkercad meshes often contain
    1-2 triangle "ghost" shells from the export process and small flat
    engraving plates that aren't useful to reconstruct.
    """
    try:
        parts = mesh.split(only_watertight=only_watertight)
    except Exception:
        return [mesh]
    if not parts:
        return [mesh]
    kept: list[trimesh.Trimesh] = []
    for p in parts:
        if len(p.faces) < min_faces:
            continue
        try:
            v = float(p.volume) if p.is_volume else 0.0
        except Exception:
            v = 0.0
        # bbox-volume fallback for non-watertight shells
        e = p.extents
        bbv = float(e[0] * e[1] * e[2])
        score = max(v, bbv * 0.1)   # accept if either watertight volume or
                                    # ~10% of bbox volume meets the bar
        if score >= min_volume:
            kept.append(p)
    return kept or [mesh]


def mesh_summary(mesh: trimesh.Trimesh) -> dict:
    bb = mesh.bounds
    return {
        "bbox_min": bb[0].tolist(),
        "bbox_max": bb[1].tolist(),
        "extents": mesh.extents.tolist(),
        "volume": float(mesh.volume) if mesh.is_volume else None,
        "n_triangles": int(len(mesh.faces)),
        "is_watertight": bool(mesh.is_watertight),
    }
