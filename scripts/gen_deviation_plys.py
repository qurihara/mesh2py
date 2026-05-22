"""Run mesh2py on every STL in a directory and write per-model
deviation PLYs to a chosen output directory.

Convenience driver around mesh2py.py — uses --deviation-ply per file
and prints a compact summary so the user can decide which PLY to
open in MeshLab / Blender first.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("indir", type=Path, help="directory of .stl files")
    ap.add_argument("--outdir", type=Path, default=Path("output/single_deviation"),
                    help="where to write per-model .ply + generated .py")
    ap.add_argument("--slice-step", type=float, default=0.2)
    ap.add_argument("--deviation-clip", type=float, default=1.0,
                    help="upper bound (mm) of the blue->green->red color ramp")
    args = ap.parse_args()

    args.outdir.mkdir(parents=True, exist_ok=True)
    stls = sorted(p for p in args.indir.iterdir() if p.suffix.lower() == ".stl")
    if not stls:
        print(f"No STL files in {args.indir}", file=sys.stderr)
        return 1

    here = Path(__file__).resolve().parent
    mesh2py = here / "mesh2py.py"

    summary: list[tuple[str, str]] = []
    for stl in stls:
        name = stl.stem
        py = args.outdir / f"{name}.py"
        rec = args.outdir / f"{name}_rec.stl"
        ply = args.outdir / f"{name}_deviation.ply"
        cmd = [
            sys.executable, str(mesh2py), str(stl),
            "-o", str(py),
            "--reconstructed-stl", str(rec),
            "--deviation-ply", str(ply),
            "--deviation-clip", str(args.deviation_clip),
            "--slice-step", str(args.slice_step),
        ]
        print(f"\n=== {name} ===", flush=True)
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        # Extract the bottom-line metrics from mesh2py's deviation report
        last = ""
        for line in cp.stdout.splitlines():
            l = line.strip()
            if l.startswith(("Hausdorff", "p90", "volume", "%|d|")):
                last += " | " + l
        ok = (cp.returncode == 0) and ply.exists()
        status = "OK" if ok else "FAIL"
        summary.append((name, status))
        if last:
            print(f"  -> {status}{last}", flush=True)
        else:
            print(f"  -> {status}", flush=True)
            if not ok:
                tail = (cp.stderr or cp.stdout)[-400:].strip().replace("\n", " | ")
                print(f"  err: {tail}", flush=True)

    print()
    print("=" * 60)
    print(f"Wrote {len([s for s in summary if s[1] == 'OK'])} / {len(summary)} PLYs to {args.outdir}")
    for name, status in summary:
        ply = args.outdir / f"{name}_deviation.ply"
        flag = "OK" if status == "OK" else "--"
        loc = str(ply) if ply.exists() else "(missing)"
        print(f"  [{flag}] {name}  ->  {loc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
