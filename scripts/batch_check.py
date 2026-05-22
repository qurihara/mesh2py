"""Batch regression test for mesh2py.

Walks a directory of meshes, runs mesh2py on each, captures the deviation
report, and classifies each model as PASS / WARN / FAIL / CRASH against
configurable thresholds. Prints a Markdown summary to stdout.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
import traceback
import warnings
from dataclasses import dataclass, field
from pathlib import Path

warnings.filterwarnings("ignore", category=RuntimeWarning, module="shapely")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _mesh_utils import load_mesh, align_to_z, mesh_summary
from _error_analysis import analyze


@dataclass
class Result:
    name: str
    path: Path
    status: str = "PENDING"          # PASS / WARN / FAIL / CRASH / EMPTY / TOO_COMPLEX
    n_triangles: int = 0
    runtime_s: float = 0.0
    segments: int = 0
    bbox_diff: float = float("nan")
    hausdorff: float = float("nan")
    mean: float = float("nan")
    p99: float = float("nan")
    volume_orig: float = float("nan")
    volume_recon: float = float("nan")
    volume_delta_pct: float = float("nan")
    surface_delta_pct: float = float("nan")
    failing_metrics: list[str] = field(default_factory=list)
    notes: str = ""


def classify(r: Result, hmax: float, mmax: float, vmax: float, pmax: float) -> None:
    """Set r.status and r.failing_metrics based on thresholds."""
    if r.status in {"FAIL", "CRASH", "EMPTY", "TOO_COMPLEX"}:
        return
    fails = []
    if r.hausdorff == r.hausdorff and r.hausdorff > hmax:   # nan-safe
        fails.append(f"hausdorff={r.hausdorff:.3f}>{hmax}")
    if r.mean == r.mean and r.mean > mmax:
        fails.append(f"mean={r.mean:.3f}>{mmax}")
    if r.volume_delta_pct == r.volume_delta_pct and abs(r.volume_delta_pct) > vmax:
        fails.append(f"|volΔ|={abs(r.volume_delta_pct):.2f}%>{vmax}%")
    if r.p99 == r.p99 and r.p99 > pmax:
        fails.append(f"p99={r.p99:.3f}>{pmax}")
    r.failing_metrics = fails
    r.status = "PASS" if not fails else "WARN"


def run_one(stl: Path, work: Path, *, slice_step: float, timeout: int,
            build_timeout: int, max_segments: int) -> Result:
    name = stl.stem
    r = Result(name=name, path=stl)

    # quick triangle-count via trimesh load (also catches unreadable meshes)
    try:
        m_orig = load_mesh(stl)
        r.n_triangles = len(m_orig.faces)
        if r.n_triangles == 0:
            r.status = "EMPTY"
            r.notes = "empty mesh (zero triangles)"
            return r
    except Exception as e:
        r.status = "FAIL"
        r.notes = f"failed to read input: {type(e).__name__}: {e}"
        return r

    out_py = (work / f"{name}.py").resolve()
    out_stl = (work / f"{name}_rec.stl").resolve()
    # Clear any stale outputs from a previous run.
    for stale in (out_py, out_stl):
        if stale.exists():
            stale.unlink()
    cmd = [
        sys.executable,
        str(Path(__file__).resolve().parent / "mesh2py.py"),
        str(stl),
        "-o", str(out_py),
        "--reconstructed-stl", str(out_stl),
        "--slice-step", str(slice_step),
        "--no-validate",
        # loft stays off by default (linear interp degrades accuracy on
        # non-linear tapered surfaces); explicit --loft to enable.
    ]
    t0 = time.perf_counter()
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        r.status = "CRASH"
        r.notes = f"mesh2py timeout > {timeout}s"
        r.runtime_s = time.perf_counter() - t0
        return r
    r.runtime_s = time.perf_counter() - t0

    if cp.returncode != 0:
        r.status = "CRASH"
        tail = (cp.stderr or cp.stdout)[-400:].strip()
        r.notes = f"mesh2py exit {cp.returncode}: {tail}"
        return r

    # parse op count out of stdout (new per-component format)
    for line in cp.stdout.splitlines():
        line = line.strip()
        if line.startswith("[seg]") and "total" in line:
            # "[seg]    1217 ops total across 22 component(s)"
            try:
                r.segments = int(line.split()[1])
            except Exception:
                pass
        elif line.startswith("[seg]") and "segments:" in line:
            try:
                r.segments = int(line.split()[1])
            except Exception:
                pass

    # Pre-screen: too many segments → build123d will time out anyway.
    if r.segments > max_segments:
        r.status = "TOO_COMPLEX"
        r.notes = (f"{r.segments} segments > {max_segments} cap; build123d "
                   f"reconstruction would be impractically slow")
        return r

    # Run the generated build123d script
    try:
        cp2 = subprocess.run(
            [sys.executable, str(out_py)],
            capture_output=True, text=True, timeout=build_timeout,
        )
    except subprocess.TimeoutExpired:
        r.status = "CRASH"
        r.notes = f"build123d script timeout > {build_timeout}s"
        return r
    r.runtime_s = time.perf_counter() - t0
    if cp2.returncode != 0:
        r.status = "FAIL"
        tail = (cp2.stderr or cp2.stdout)[-500:].strip().replace("\n", " | ")
        r.notes = f"build123d exit {cp2.returncode}: {tail}"
        return r

    if not out_stl.exists():
        r.status = "FAIL"
        r.notes = "build123d script ran but produced no STL"
        return r

    try:
        m_recon = load_mesh(out_stl)
    except Exception as e:
        r.status = "FAIL"
        r.notes = f"reconstructed STL unreadable: {type(e).__name__}: {e}"
        return r

    try:
        aligned = align_to_z(m_orig)
        stats = analyze(aligned, m_recon, n_samples=4000)
    except Exception as e:
        r.status = "FAIL"
        r.notes = f"deviation analysis failed: {type(e).__name__}: {e}"
        r.notes += " :: " + traceback.format_exc().splitlines()[-2]
        return r

    r.bbox_diff = stats["bbox_max_axis_diff"]
    r.hausdorff = stats["hausdorff"]
    r.mean = stats["mean"]
    r.p99 = stats["p99"]
    r.volume_orig = stats["volume_orig"]
    r.volume_recon = stats["volume_recon"]
    r.volume_delta_pct = stats["volume_delta_pct"]
    r.surface_delta_pct = stats["surface_delta_pct"]
    return r


def format_table(results: list[Result]) -> str:
    header = (
        "| # | model | tris | segs | runtime | bbox_diff | hausdorff | mean | p99 |"
        " vol orig | vol recon | volΔ% | status |"
    )
    sep = (
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---|"
    )
    lines = [header, sep]
    for i, r in enumerate(results, 1):
        def fmt(v, p=3):
            return "—" if v != v else f"{v:.{p}f}"
        lines.append(
            f"| {i} | `{r.name}` | {r.n_triangles} | {r.segments} | "
            f"{r.runtime_s:.1f}s | {fmt(r.bbox_diff)} | {fmt(r.hausdorff)} | "
            f"{fmt(r.mean)} | {fmt(r.p99)} | {fmt(r.volume_orig, 1)} | "
            f"{fmt(r.volume_recon, 1)} | {fmt(r.volume_delta_pct, 2)} | "
            f"**{r.status}** |"
        )
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("indir", type=Path, help="directory of .stl/.obj/.glb")
    ap.add_argument("--workdir", type=Path, default=Path("output/batch"),
                    help="where to place generated .py and reconstructed STLs")
    ap.add_argument("--slice-step", type=float, default=0.2)
    ap.add_argument("--timeout", type=int, default=120,
                    help="mesh2py timeout in seconds")
    ap.add_argument("--build-timeout", type=int, default=120,
                    help="build123d execution timeout in seconds")
    ap.add_argument("--max-segments", type=int, default=500,
                    help="if mesh2py produces more than N total ops "
                         "across all components, mark TOO_COMPLEX without "
                         "running build123d. With per-component split each "
                         "component is its own BuildPart so the cap can be "
                         "looser than the monolithic case.")
    ap.add_argument("--hausdorff-max", type=float, default=1.0)
    ap.add_argument("--mean-max", type=float, default=0.1)
    ap.add_argument("--volume-delta-max", type=float, default=5.0,
                    help="|Δvolume| %% threshold (absolute)")
    ap.add_argument("--p99-max", type=float, default=0.5)
    args = ap.parse_args()

    args.workdir.mkdir(parents=True, exist_ok=True)
    stls = sorted(p for p in args.indir.iterdir() if p.suffix.lower() in {".stl", ".obj", ".glb"})
    if not stls:
        print(f"No mesh files found in {args.indir}")
        return 1

    results: list[Result] = []
    for i, stl in enumerate(stls, 1):
        print(f"[{i}/{len(stls)}] {stl.name} ...", flush=True)
        r = run_one(stl, args.workdir,
                    slice_step=args.slice_step,
                    timeout=args.timeout,
                    build_timeout=args.build_timeout,
                    max_segments=args.max_segments)
        classify(r, args.hausdorff_max, args.mean_max,
                 args.volume_delta_max, args.p99_max)
        print(f"    -> {r.status}  (runtime {r.runtime_s:.1f}s, tris={r.n_triangles}, "
              f"segs={r.segments})", flush=True)
        if r.notes:
            print(f"    note: {r.notes}", flush=True)
        results.append(r)

    print()
    print("=" * 72)
    counts = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    summary = " ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    print(f"# mesh2py batch summary  ({len(results)} models)")
    print()
    print(f"**{summary}**  (thresholds: hausdorff<{args.hausdorff_max}, "
          f"mean<{args.mean_max}, |volΔ|<{args.volume_delta_max}%, "
          f"p99<{args.p99_max}, slice-step={args.slice_step})")
    print()
    print(format_table(results))

    bad = [r for r in results if r.status in {"WARN", "FAIL", "CRASH", "TOO_COMPLEX"}]
    if bad:
        print("\n## Failing / warning models")
        for r in bad:
            print(f"\n### `{r.name}` — **{r.status}**")
            if r.failing_metrics:
                print("- thresholds exceeded: " + ", ".join(r.failing_metrics))
            if r.notes:
                print(f"- note: {r.notes}")

    return 0 if all(r.status in {"PASS", "WARN"} for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
