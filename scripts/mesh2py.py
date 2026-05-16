"""mesh2py — convert a 3D mesh (.stl / .obj / .glb) into a build123d
Python script using Z-axis slicing + per-segment extrusion.

Primary input is STL (the standard 3D-print format); OBJ and GLB are
also accepted because trimesh handles them transparently. The generated
script is the editable "prompt": run it to recreate the original
geometry, then edit dimensions or refactor segments into smooth
primitives (Cylinder, loft, revolve, fillet) to evolve the design."""
from __future__ import annotations

import argparse
import subprocess
import sys
import warnings
from pathlib import Path

# silence shapely's "invalid value encountered in oriented_envelope" warnings
# from degenerate slice fragments.
warnings.filterwarnings("ignore", category=RuntimeWarning, module="shapely")

# allow `python scripts/mesh2py.py ...` without setting PYTHONPATH
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _mesh_utils import load_mesh, align_to_z, slice_z, simplify_polys, mesh_summary
from _feature_detect import segment_slices
from _codegen import render_script
from _error_analysis import analyze, format_report, write_deviation_ply


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input", type=Path, help="mesh file (.stl/.obj/.glb)")
    ap.add_argument("-o", "--output", type=Path, required=True,
                    help="output .py path")
    ap.add_argument("--slice-step", type=float, default=0.2,
                    help="Z slice spacing in mm (default 0.2, matching the "
                         "standard 3D printer layer height; typical range 0.08-0.24)")
    ap.add_argument("--simplify", type=float, default=0.05,
                    help="shapely simplify tolerance in mm (default 0.05)")
    ap.add_argument("--merge-tol", type=float, default=None,
                    help="Hausdorff distance (mm) under which consecutive slices "
                         "are merged into one extrude segment (default: 1.5*slice_step)")
    ap.add_argument("--axis", choices=["x", "y", "z"], default=None,
                    help="force this axis as the slicing/thickness direction")
    ap.add_argument("--reconstructed-stl", type=Path,
                    default=Path("output/reconstructed.stl"),
                    help="path the generated script will export to")
    ap.add_argument("--no-validate", action="store_true",
                    help="skip running the generated script + deviation analysis")
    ap.add_argument("--deviation-ply", type=Path, default=None,
                    help="if set, write a vertex-colored PLY of the reconstructed "
                         "mesh whose colors encode distance to the original "
                         "(blue=0, green=mid, red>=clip)")
    ap.add_argument("--deviation-clip", type=float, default=1.0,
                    help="color-ramp upper bound (mm) for --deviation-ply")
    ap.add_argument("--samples", type=int, default=8000,
                    help="surface samples per side for deviation analysis")
    args = ap.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.reconstructed_stl.parent.mkdir(parents=True, exist_ok=True)

    print(f"[load]   {args.input}")
    mesh = load_mesh(args.input)
    print(f"[load]   triangles={len(mesh.faces)} bounds_min={mesh.bounds[0]} bounds_max={mesh.bounds[1]}")

    print(f"[align]  axis={args.axis or 'auto (min-variance -> Z)'}")
    aligned = align_to_z(mesh, args.axis)
    summary = mesh_summary(aligned)
    print(f"[align]  extents={aligned.extents}")

    print(f"[slice]  step={args.slice_step}")
    slices = slice_z(aligned, args.slice_step)
    print(f"[slice]  {len(slices)} non-empty layers")

    print(f"[simp]   tol={args.simplify}")
    for s in slices:
        s.polygons = simplify_polys(s.polygons, args.simplify)

    print(f"[seg]    segmenting (merge_tol={args.merge_tol or 'auto'})...")
    segments = segment_slices(slices, args.slice_step, merge_tol=args.merge_tol)
    print(f"[seg]    {len(segments)} segments:")
    for i, s in enumerate(segments):
        kinds = [f.kind for f in s.rep_features]
        print(f"           seg{i:>2}: z={s.z_lo:6.3f}..{s.z_hi:6.3f} "
              f"h={s.z_hi - s.z_lo:6.3f}  features={kinds}")

    print(f"[gen]    -> {args.output}")
    code = render_script(
        source_path=str(args.input),
        summary=summary,
        segments=segments,
        slice_step=args.slice_step,
        out_stl_path=str(args.reconstructed_stl),
    )
    args.output.write_text(code, encoding="utf-8")
    print("[gen]    done.")

    if not args.no_validate:
        print(f"[valid]  running generated script -> {args.reconstructed_stl}")
        cp = subprocess.run(
            [sys.executable, str(args.output)],
            capture_output=True, text=True,
        )
        if cp.returncode != 0:
            print("[valid]  FAILED running script:")
            print(cp.stdout)
            print(cp.stderr)
            return 1
        print(cp.stdout.strip())

        recon = load_mesh(args.reconstructed_stl)
        stats = analyze(aligned, recon, n_samples=args.samples)
        print(format_report(stats))

        if args.deviation_ply is not None:
            print(f"[valid]  writing colored deviation PLY -> {args.deviation_ply}")
            write_deviation_ply(recon, aligned, args.deviation_ply,
                                clip=args.deviation_clip)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
