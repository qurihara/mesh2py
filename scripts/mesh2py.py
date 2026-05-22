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

from _mesh_utils import (
    load_mesh, align_to_z, slice_z, simplify_polys, strip_small_holes,
    resample_polys, mesh_summary, split_components,
)
from _feature_detect import segment_slices, group_lofts, detect_primitive
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
    ap.add_argument("--no-split", action="store_true",
                    help="skip connected-component decomposition; treat the "
                         "whole mesh as one part (legacy behavior)")
    ap.add_argument("--min-component-volume", type=float, default=50.0,
                    help="discard connected components smaller than this volume "
                         "(mm^3) — filters out floating triangles / engraved noise")
    ap.add_argument("--min-component-faces", type=int, default=50,
                    help="discard components with fewer than this many triangles")
    ap.add_argument("--no-loft", action="store_true",
                    help="disable loft compression of similar-topology consecutive segments")
    ap.add_argument("--no-primitive", action="store_true",
                    help="disable Cylinder/revolve primitive detection per component")
    ap.add_argument("--drop-hole-area", type=float, default=5.0,
                    help="ignore interior holes whose area is below this "
                         "(mm^2). Default 5.0 mm^2 (drops engraved letter text).")
    ap.add_argument("--drop-outer-area", type=float, default=2.0,
                    help="drop standalone outer polygons below this area "
                         "(mm^2). Default 2.0 mm^2 (drops floating speckles).")
    ap.add_argument("--resample", type=int, default=0,
                    help="resample each contour ring to N points evenly spaced "
                         "along arc length so adjacent slices share vertex count "
                         "and topology (enables loft compression). 0 disables. "
                         "WARNING: resampling can break OCCT loft on shapes with "
                         "long straight edges; recommended only for curved meshes.")
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

    if args.no_split:
        components = [aligned]
    else:
        components = split_components(
            aligned,
            min_volume=args.min_component_volume,
            min_faces=args.min_component_faces,
        )
    print(f"[split]  {len(components)} component(s) "
          f"(volumes: {[round(c.volume, 1) if c.is_volume else None for c in components]})")

    per_comp_groups: list = []
    n_primitive_total = 0
    for ci, comp in enumerate(components):
        print(f"[comp {ci}] tris={len(comp.faces)} extents={comp.extents}")
        slices = slice_z(comp, args.slice_step)
        for s in slices:
            s.polygons = simplify_polys(s.polygons, args.simplify)
            if args.drop_hole_area > 0:
                s.polygons = strip_small_holes(s.polygons, args.drop_hole_area)
            if args.resample > 0:
                s.polygons = resample_polys(s.polygons, args.resample)

        # Primitive detection short-circuit: rotationally symmetric component
        # -> emit Cylinder/revolve directly, skipping slice/loft pipeline.
        if not args.no_primitive:
            z_bounds = (float(comp.bounds[0, 2]), float(comp.bounds[1, 2]))
            prim = detect_primitive(slices, z_bounds=z_bounds)
            if prim is not None:
                kind = type(prim).__name__
                print(f"[comp {ci}] primitive detected: {kind} "
                      f"(axis=({prim.cx:.2f}, {prim.cy:.2f}), z={prim.z_lo:.2f}..{prim.z_hi:.2f})")
                per_comp_groups.append([prim])
                n_primitive_total += 1
                continue

        segments = segment_slices(
            slices, args.slice_step, merge_tol=args.merge_tol,
            drop_hole_area=args.drop_hole_area,
            drop_outer_area=args.drop_outer_area,
        )
        if args.no_loft:
            groups = segments
        else:
            groups = group_lofts(segments)
        n_loft = sum(1 for g in groups if hasattr(g, "features_lo"))
        print(f"[comp {ci}] {len(slices)} layers -> {len(segments)} segs "
              f"-> {len(groups)} ops ({n_loft} loft)")
        per_comp_groups.append(groups)
    if n_primitive_total:
        print(f"[prim]   {n_primitive_total}/{len(components)} components "
              f"reduced to primitives (Cylinder/revolve)")

    total_ops = sum(len(g) for g in per_comp_groups)
    print(f"[seg]    {total_ops} ops total across {len(components)} component(s)")

    print(f"[gen]    -> {args.output}")
    code = render_script(
        source_path=str(args.input),
        summary=summary,
        components=per_comp_groups,
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
