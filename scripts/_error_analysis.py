"""Deviation analysis between original mesh and reconstructed mesh."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import trimesh


def _signed_distances(target: trimesh.Trimesh, points: np.ndarray) -> np.ndarray:
    """Distance from each point to nearest surface of `target`.
    Positive when point is outside the target, negative when inside.
    """
    _, dist, _ = trimesh.proximity.closest_point(target, points)
    inside = target.contains(points)
    return np.where(inside, -dist, dist)


def _text_histogram(values: np.ndarray, n_bins: int = 16, width: int = 40) -> str:
    """Compact horizontal text histogram."""
    if len(values) == 0:
        return "(no samples)"
    lo, hi = float(values.min()), float(values.max())
    if hi - lo < 1e-12:
        return f"  [{lo:+.3f}] {len(values)} samples (all identical)"
    edges = np.linspace(lo, hi, n_bins + 1)
    counts, _ = np.histogram(values, bins=edges)
    peak = counts.max() if counts.size else 1
    lines = []
    for i in range(n_bins):
        bar = "#" * int(round(counts[i] / peak * width))
        lines.append(f"  [{edges[i]:+7.3f} .. {edges[i+1]:+7.3f}] {counts[i]:5d} | {bar}")
    return "\n".join(lines)


def analyze(
    original: trimesh.Trimesh,
    reconstructed: trimesh.Trimesh,
    *,
    n_samples: int = 8000,
    colored_ply_path: Path | None = None,
) -> dict:
    """Compute symmetric deviation stats between two meshes.

    Both meshes should already be in the same coordinate frame.
    """
    bb_o, bb_r = original.extents, reconstructed.extents
    v_o = float(original.volume) if original.is_volume else float("nan")
    v_r = float(reconstructed.volume) if reconstructed.is_volume else float("nan")
    sa_o = float(original.area)
    sa_r = float(reconstructed.area)

    # Symmetric Hausdorff via surface sampling in both directions.
    pts_o = original.sample(n_samples)
    pts_r = reconstructed.sample(n_samples)
    d_o_to_r = _signed_distances(reconstructed, pts_o)   # orig sample -> recon surface
    d_r_to_o = _signed_distances(original, pts_r)        # recon sample -> orig surface

    abs_o = np.abs(d_o_to_r)
    abs_r = np.abs(d_r_to_o)
    both = np.concatenate([abs_o, abs_r])

    stats = {
        "bbox_extents_orig": bb_o.tolist(),
        "bbox_extents_recon": bb_r.tolist(),
        "bbox_max_axis_diff": float(np.max(np.abs(np.asarray(bb_o) - np.asarray(bb_r)))),
        "volume_orig": v_o,
        "volume_recon": v_r,
        "volume_delta_pct": (v_r - v_o) / v_o * 100.0 if v_o else float("nan"),
        "surface_orig": sa_o,
        "surface_recon": sa_r,
        "surface_delta_pct": (sa_r - sa_o) / sa_o * 100.0 if sa_o else float("nan"),
        "n_samples_each_side": n_samples,
        "hausdorff": float(both.max()),
        "mean": float(both.mean()),
        "rms": float(np.sqrt((both ** 2).mean())),
        "median": float(np.median(both)),
        "p90": float(np.percentile(both, 90)),
        "p95": float(np.percentile(both, 95)),
        "p99": float(np.percentile(both, 99)),
        "signed_mean_o_to_r": float(d_o_to_r.mean()),
        "signed_mean_r_to_o": float(d_r_to_o.mean()),
        "pct_above_layer_height_o": float((abs_o > 0.2).mean() * 100.0),
        "histogram_signed_o_to_r": _text_histogram(d_o_to_r),
        "histogram_abs_combined": _text_histogram(both),
    }

    if colored_ply_path is not None:
        _write_colored_ply(reconstructed, colored_ply_path)
    return stats


def _write_colored_ply(mesh: trimesh.Trimesh, path: Path) -> None:
    """Color each vertex of `mesh` by its closest-point distance to itself.
    (Stub: in practice we want distance vs the *other* mesh; the caller
    should pre-compute. Kept here as utility wrapper.)"""
    # No-op placeholder; the caller has access to both meshes already.
    raise NotImplementedError


def write_deviation_ply(
    src_mesh: trimesh.Trimesh,
    ref_mesh: trimesh.Trimesh,
    path: Path,
    *,
    clip: float = 1.0,
) -> None:
    """Save `src_mesh` as a PLY whose vertices are colored by distance to
    `ref_mesh`. Color ramp: blue (close) -> green -> red (>= clip mm)."""
    pts = src_mesh.vertices
    _, d, _ = trimesh.proximity.closest_point(ref_mesh, pts)
    t = np.clip(d / clip, 0.0, 1.0)
    # blue -> green -> red ramp
    r = np.clip(2 * t - 1, 0, 1)
    g = np.clip(1 - np.abs(2 * t - 1), 0, 1)
    b = np.clip(1 - 2 * t, 0, 1)
    colors = (np.stack([r, g, b, np.ones_like(r)], axis=1) * 255).astype(np.uint8)
    out = src_mesh.copy()
    out.visual.vertex_colors = colors
    path.parent.mkdir(parents=True, exist_ok=True)
    out.export(str(path))


def format_report(stats: dict) -> str:
    bb_o = stats["bbox_extents_orig"]
    bb_r = stats["bbox_extents_recon"]
    return "\n".join([
        "=" * 60,
        "Deviation report (original vs reconstructed)",
        "=" * 60,
        f"bbox extents (mm)",
        f"    original     : {bb_o[0]:8.3f} x {bb_o[1]:8.3f} x {bb_o[2]:8.3f}",
        f"    reconstructed: {bb_r[0]:8.3f} x {bb_r[1]:8.3f} x {bb_r[2]:8.3f}",
        f"    max axis diff: {stats['bbox_max_axis_diff']:.4f} mm",
        "",
        f"volume        : orig={stats['volume_orig']:10.3f}  recon={stats['volume_recon']:10.3f}"
        f"  (Δ={stats['volume_delta_pct']:+6.2f} %)",
        f"surface area  : orig={stats['surface_orig']:10.3f}  recon={stats['surface_recon']:10.3f}"
        f"  (Δ={stats['surface_delta_pct']:+6.2f} %)",
        "",
        f"surface-to-surface distance (|d|) over "
        f"{stats['n_samples_each_side']} samples per side:",
        f"    Hausdorff (max) : {stats['hausdorff']:.4f} mm",
        f"    mean            : {stats['mean']:.4f} mm",
        f"    RMS             : {stats['rms']:.4f} mm",
        f"    median          : {stats['median']:.4f} mm",
        f"    p90 / p95 / p99 : {stats['p90']:.4f} / {stats['p95']:.4f} / {stats['p99']:.4f} mm",
        f"    %|d|>0.2mm (orig-side): {stats['pct_above_layer_height_o']:.2f} %",
        "",
        f"signed mean (orig->recon): {stats['signed_mean_o_to_r']:+.4f} mm  "
        f"(negative => recon is inside orig)",
        f"signed mean (recon->orig): {stats['signed_mean_r_to_o']:+.4f} mm",
        "",
        "histogram of signed distance (orig sample -> recon surface):",
        stats["histogram_signed_o_to_r"],
        "",
        "histogram of |distance| (combined both sides):",
        stats["histogram_abs_combined"],
        "=" * 60,
    ])
