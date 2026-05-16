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


def align_to_z(mesh: trimesh.Trimesh, axis: str | None = None) -> trimesh.Trimesh:
    """Rotate so the minimum-variance axis is Z (the "thickness" axis),
    then translate so min corner is at origin."""
    m = mesh.copy()
    if axis in {"x", "y", "z"}:
        idx = {"x": 0, "y": 1, "z": 2}[axis]
    else:
        ext = m.extents
        idx = int(np.argmin(ext))
    if idx != 2:
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
