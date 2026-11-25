"""Write results: one GeoPackage (instead of 880 loose shapefiles), a CSV summary,
BEA-compatible start/end vertex layers and a run manifest.

Fixes: dist_m was always 0 (now length_m in metres + cost, climb, slope stats);
the CRS is stored in the layer (not a hand-written .prj); every origin has a stable id
and a status, so missing routes are explained instead of silently absent.
"""
from __future__ import annotations

import hashlib
import json
import platform
import subprocess
from datetime import datetime
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString, Point

from .routing import Route
from .surface import Surface

FIELDS = ["fid2", "origin_id", "status", "length_m", "cost", "climb_m", "descent_m",
          "max_slope_deg", "mean_slope_deg", "snapped_m", "edge_limited", "buffer_m", "tile",
          "origin_x", "origin_y", "road_x", "road_y", "n_vertices"]


def routes_frame(routes: list[Route], origins: gpd.GeoDataFrame, surface: Surface,
                 simplify_m: float = 0.0) -> gpd.GeoDataFrame:
    grid = surface.grid
    recs, geoms = [], []
    for route, (_, o) in zip(routes, origins.iterrows()):
        rec = {f: getattr(route, f) for f in FIELDS if hasattr(route, f)}
        rec["origin_x"], rec["origin_y"] = o.geometry.x, o.geometry.y
        rec["road_x"] = rec["road_y"] = np.nan
        rec["n_vertices"] = len(route.cells)
        geom = None
        if route.cells:
            r = np.array([c[0] for c in route.cells])
            c = np.array([c[1] for c in route.cells])
            x, y = grid.xy(r, c)
            rec["road_x"], rec["road_y"] = float(x[-1]), float(y[-1])
            if len(x) > 1:
                pts = list(zip(x, y))
                if route.snapped_m > 0:
                    pts = [(o.geometry.x, o.geometry.y)] + pts
                geom = LineString(pts)
                if simplify_m > 0:
                    geom = geom.simplify(simplify_m)
        recs.append(rec)
        geoms.append(geom)
    gdf = gpd.GeoDataFrame(recs, geometry=geoms, crs=grid.crs.to_wkt())
    return gdf[FIELDS + ["geometry"]]


def write_outputs(paths: gpd.GeoDataFrame, origins: gpd.GeoDataFrame, run_dir: Path,
                  cfg: dict) -> dict:
    out = cfg["output"]
    gpkg = run_dir / out["gpkg_name"]
    paths.to_file(gpkg, layer="paths", driver="GPKG")
    pts = origins.merge(paths.drop(columns="geometry"), on=["fid2", "origin_id"])
    pts.to_file(gpkg, layer="origins", driver="GPKG")
    if out["write_wgs84"]:
        paths.to_crs(4326).to_file(gpkg, layer="paths_wgs84", driver="GPKG")
        pts.to_crs(4326).to_file(gpkg, layer="origins_wgs84", driver="GPKG")
    paths.drop(columns="geometry").to_csv(run_dir / "virtual_roads_summary.csv", index=False)
    written = {"gpkg": str(gpkg)}
    if out["export_bea_vertices"]:
        written.update(export_bea_vertices(paths, run_dir))
    return written


def export_bea_vertices(paths: gpd.GeoDataFrame, run_dir: Path) -> dict:
    """Start (origin) and end (road) vertices in the format BEA_Feasibility_model_test reads:
    Point geometries (not MultiPoint), EPSG:4326, fields fid2 and length (metres)."""
    ok = paths[paths.status.isin(["ok", "on_road"])].copy()
    ok["length"] = ok.length_m.fillna(0).round(1)
    start = gpd.GeoDataFrame(ok[["fid2", "length"]], crs=paths.crs,
                             geometry=gpd.points_from_xy(ok.origin_x, ok.origin_y)).to_crs(4326)
    end = gpd.GeoDataFrame(ok[["fid2", "length"]], crs=paths.crs,
                           geometry=gpd.points_from_xy(ok.road_x, ok.road_y)).to_crs(4326)
    s, e = run_dir / "bea_start_vertices.gpkg", run_dir / "bea_end_vertices.gpkg"
    start.to_file(s, driver="GPKG")
    end.to_file(e, driver="GPKG")
    return {"bea_start_vertices": str(s), "bea_end_vertices": str(e)}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def input_hashes(cfg: dict) -> dict:
    hashes = {}
    for key in ("dem", "roads", "grid_template", "roi"):
        p = cfg["paths"][key]
        if p and Path(p).exists():
            hashes[p] = _sha256(Path(p))
    return hashes


def git_commit(repo: Path) -> str:
    try:
        return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True,
                              text=True, timeout=10).stdout.strip()
    except Exception:  # git missing is not an error for a model run
        return ""


def write_manifest(run_dir: Path, cfg: dict, started: datetime, stats: dict, repo: Path) -> None:
    import geopandas, networkx, numpy, rasterio, shapely, skimage  # noqa: E401
    from osgeo import gdal
    manifest = {
        "model": "Virtual_roads",
        "status": stats.get("status", "complete"),
        "started": started.isoformat(timespec="seconds"),
        "finished": datetime.now().isoformat(timespec="seconds"),
        "git_commit": git_commit(repo),
        "python": platform.python_version(),
        "libraries": {"gdal": gdal.__version__, "geopandas": geopandas.__version__,
                      "numpy": numpy.__version__, "rasterio": rasterio.__version__,
                      "shapely": shapely.__version__, "scikit-image": skimage.__version__,
                      "networkx": networkx.__version__},
        "inputs_sha256": input_hashes(cfg),
        "config": cfg,
        "results": stats,
    }
    (run_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2, default=str))


def status_counts(paths: pd.DataFrame) -> dict:
    return {str(k): int(v) for k, v in paths.status.value_counts().items()}
