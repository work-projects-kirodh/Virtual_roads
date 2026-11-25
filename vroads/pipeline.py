"""One unbroken run: cost surface -> origins -> routing -> outputs -> manifest."""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from pathlib import Path

from . import outputs, points, routing, surface

log = logging.getLogger("vroads")
REPO = Path(__file__).resolve().parent.parent


def resolve_paths(cfg: dict, base: Path) -> None:
    """Make relative input/output paths relative to ``base`` (the repo by default), so the
    model works from any working directory (original relied on os.chdir / cwd)."""
    for key, value in cfg["paths"].items():
        if key == "run_name" or not value:
            continue
        p = Path(value)
        if not p.is_absolute():
            cfg["paths"][key] = str((base / p).resolve())


def prepare_run_dir(cfg: dict) -> Path:
    name = cfg["paths"]["run_name"] or datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = Path(cfg["paths"]["output_dir"]) / name
    if run_dir.exists() and any(run_dir.iterdir()) and not cfg["run"]["overwrite"]:
        raise FileExistsError(f"{run_dir} already exists; choose another run_name or set "
                              "run.overwrite = true")
    (run_dir / "work").mkdir(parents=True, exist_ok=True)
    return run_dir


def setup_logging(run_dir: Path, level: str) -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())
    for h in list(root.handlers):
        if getattr(h, "_vroads", False):
            root.removeHandler(h)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S")
    for h in (logging.StreamHandler(), logging.FileHandler(run_dir / "run.log", encoding="utf8")):
        h.setFormatter(fmt)
        h._vroads = True
        root.addHandler(h)
    for noisy in ("pyogrio", "fiona", "rasterio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def check_inputs(cfg: dict) -> None:
    missing = [f"paths.{k} = {cfg['paths'][k]}" for k in ("dem", "roads", "grid_template", "roi")
               if cfg["paths"][k] and not Path(cfg["paths"][k]).exists()]
    if missing:
        raise FileNotFoundError("Input files not found:\n  " + "\n  ".join(missing))


def run(cfg: dict, base: Path = REPO) -> Path:
    started, t0 = datetime.now(), time.perf_counter()
    resolve_paths(cfg, base)
    check_inputs(cfg)
    run_dir = prepare_run_dir(cfg)
    setup_logging(run_dir, cfg["run"]["log_level"])
    log.info("Virtual roads run -> %s", run_dir)
    stats: dict = {"status": "failed"}
    try:
        t = time.perf_counter()
        surf = surface.build_surface(cfg, run_dir / "work")
        stats["seconds_surface"] = round(time.perf_counter() - t, 2)

        crs = surf.grid.crs
        roi = surface.read_vector(cfg["paths"]["roi"], crs) if cfg["paths"]["roi"] else None
        origins = points.make_origins(cfg, surf.grid, roi)
        log.info("%d origin points (spacing %.0f m)", len(origins), cfg["points"]["origin_spacing_m"])

        t = time.perf_counter()
        routes = routing.route_all(surf, origins, cfg)
        stats["seconds_routing"] = round(time.perf_counter() - t, 2)

        paths = outputs.routes_frame(routes, origins, surf, cfg["output"]["simplify_tolerance_m"])
        stats["files"] = outputs.write_outputs(paths, origins, run_dir, cfg)
        stats["origins"] = len(origins)
        stats["status_counts"] = outputs.status_counts(paths)
        ok = paths[paths.status == "ok"]
        stats["length_m"] = {"mean": round(float(ok.length_m.mean()), 1) if len(ok) else None,
                             "max": round(float(ok.length_m.max()), 1) if len(ok) else None}
        stats["status"] = "complete"
        log.info("Statuses: %s", stats["status_counts"])
        if not cfg["output"]["keep_intermediate"]:
            for f in (run_dir / "work").glob("*"):
                os.remove(f)
        return run_dir
    finally:
        stats["seconds_total"] = round(time.perf_counter() - t0, 2)
        outputs.write_manifest(run_dir, cfg, started, stats, REPO)
        log.info("Finished (%s) in %.1f s", stats["status"], stats["seconds_total"])
