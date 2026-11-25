"""Tests for the upgraded Virtual Roads model.

Run:  python -m pytest -q        (from the repository root)
The last test runs the full example (~10 s).
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
from rasterio.crs import CRS
from rasterio.transform import from_origin
from shapely.geometry import Point

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from vroads import config, pipeline, routing, surface  # noqa: E402


# ---------------------------------------------------------------- config
def test_defaults_load_and_match_config_toml():
    """config.toml must list exactly the defaults (so the file documents every setting)."""
    assert config.load_config(REPO / "config.toml") == config.load_config(None)


def test_override_parsing_and_types():
    cfg = config.load_config(None, ["surface.max_slope_deg=12", "routing.fully_connected=false",
                                    "paths.run_name=abc"])
    assert cfg["surface"]["max_slope_deg"] == 12.0
    assert cfg["routing"]["fully_connected"] is False
    assert cfg["paths"]["run_name"] == "abc"


def test_unknown_key_and_bad_value_rejected():
    with pytest.raises(config.ConfigError):
        config.load_config(None, ["surface.not_a_key=1"])
    with pytest.raises(config.ConfigError):
        config.load_config(None, ["surface.max_slope_deg=steep"])
    with pytest.raises(config.ConfigError):
        config.load_config(None, ["surface.cost_function=magic"])


# ---------------------------------------------------------------- cost function
def test_cost_functions():
    cfg = config.load_config(None)
    slope = np.array([[0.0, 7.5, 20.0, np.nan]])
    imp = np.isnan(slope) | (slope >= 15)
    c = surface.cost_from_slope(slope, imp, cfg)
    assert c[0, 0] == 1.0 and c[0, 1] == pytest.approx(1.5)
    assert np.isinf(c[0, 2]) and np.isinf(c[0, 3])          # steep and NoData impassable
    cfg["surface"]["cost_function"] = "slope_only"
    c2 = surface.cost_from_slope(slope, imp, cfg)
    assert c2[0, 0] == 0.0 and c2[0, 1] == 7.5               # original behaviour available


# ---------------------------------------------------------------- routing on synthetic grids
def _surface(cost, roads, cell=10.0):
    h, w = cost.shape
    grid = surface.Grid(CRS.from_epsg(32735), from_origin(0, h * cell, cell, cell), w, h)
    slope = np.zeros_like(cost)
    return surface.Surface(grid, np.zeros_like(cost), slope, cost, roads & np.isfinite(cost),
                           np.ones_like(roads))


def _origins(grid, cells):
    xs, ys = grid.xy(np.array([c[0] for c in cells]), np.array([c[1] for c in cells]))
    return gpd.GeoDataFrame({"fid2": range(len(cells)), "origin_id": [f"o{i}" for i in range(len(cells))]},
                            geometry=gpd.points_from_xy(xs, ys), crs=grid.crs.to_wkt())


def _cfg(**routing_over):
    cfg = config.load_config(None)
    cfg["routing"].update({"tile_buffer_m": 1000.0, "max_tile_buffer_m": 1000.0})
    cfg["routing"].update(routing_over)
    cfg["points"]["snap_origin_radius_m"] = 0.0
    return cfg


def test_route_goes_around_wall_and_reports_metres():
    cost = np.ones((11, 11))
    cost[0:9, 5] = np.inf                       # wall with a gap at the bottom
    roads = np.zeros((11, 11), bool)
    roads[:, 10] = True                         # road along the east edge
    surf = _surface(cost, roads)
    (r,) = routing.route_all(surf, _origins(surf.grid, [(0, 0)]), _cfg())
    assert r.status == "ok"
    assert all(cost[c] < np.inf for c in r.cells)
    assert r.cells[0] == (0, 0) and roads[r.cells[-1]]
    assert r.length_m > 100                     # longer than the 10-cell straight line
    assert r.length_m == pytest.approx(r.cost, rel=1e-6)   # unit cost -> cost == metres


def test_statuses_on_road_no_path_impassable_and_snap():
    cost = np.ones((9, 9))
    cost[3:6, 3:6] = np.inf
    cost[4, 4] = 1.0                            # enclosed pocket -> no path
    cost[0, 8] = np.inf                         # origin inside an obstacle
    roads = np.zeros((9, 9), bool)
    roads[8, :] = True
    surf = _surface(cost, roads)
    orig = _origins(surf.grid, [(8, 0), (4, 4), (0, 8)])
    res = routing.route_all(surf, orig, _cfg())
    assert [r.status for r in res] == ["on_road", "no_path", "origin_impassable"]
    cfg = _cfg()
    cfg["points"]["snap_origin_radius_m"] = 15.0          # 1.5 cells -> snaps to a neighbour
    res = routing.route_all(surf, orig, cfg)
    assert res[2].status == "ok" and res[2].snapped_m == pytest.approx(10.0)


def test_buffer_grows_until_road_found():
    """A road far outside the initial window is still found (adaptive buffer)."""
    cost = np.ones((5, 200))
    roads = np.zeros((5, 200), bool)
    roads[:, 199] = True
    surf = _surface(cost, roads)
    cfg = _cfg(tile_size_m=50.0, tile_buffer_m=50.0, max_tile_buffer_m=4000.0)
    (r,) = routing.route_all(surf, _origins(surf.grid, [(2, 0)]), cfg)
    assert r.status == "ok" and r.buffer_m > 50 and not r.edge_limited
    assert r.length_m == pytest.approx(1990.0)


def test_multi_source_picks_cheapest_road():
    cost = np.ones((3, 21))
    cost[:, 1:5] = 10.0                          # expensive ground towards the west road
    roads = np.zeros((3, 21), bool)
    roads[:, 0] = roads[:, 20] = True
    surf = _surface(cost, roads)
    (r,) = routing.route_all(surf, _origins(surf.grid, [(1, 6)]), _cfg())
    assert r.cells[-1][1] == 20                  # east road is cheaper although further away


# ---------------------------------------------------------------- full example
@pytest.fixture(scope="module")
def example_run(tmp_path_factory):
    out = tmp_path_factory.mktemp("vr")
    cfg = config.load_config(REPO / "config.toml",
                             [f"paths.output_dir={json.dumps(out.as_posix())}", "paths.run_name=test"])
    return pipeline.run(cfg, base=REPO)


def test_example_runs_end_to_end(example_run):
    manifest = json.loads((example_run / "run_manifest.json").read_text())
    assert manifest["status"] == "complete"
    paths = gpd.read_file(example_run / "virtual_roads.gpkg", layer="paths")
    assert len(paths) == 222                                 # every origin accounted for
    assert set(paths.status) <= {"ok", "on_road", "no_path", "origin_impassable"}
    ok = paths[paths.status == "ok"]
    assert len(ok) >= 215
    assert (ok.length_m > 0).all() and ok.geometry.is_valid.all()   # dist_m was always 0
    assert paths.origin_id.is_unique


def test_bea_vertex_export_is_point_wgs84(example_run):
    s = gpd.read_file(example_run / "bea_start_vertices.gpkg")
    e = gpd.read_file(example_run / "bea_end_vertices.gpkg")
    assert s.crs.to_epsg() == 4326 and set(s.geom_type) == {"Point"}
    assert list(s.fid2) == list(e.fid2) and {"fid2", "length"} <= set(s.columns)


def test_inputs_untouched_and_rerun_refused(example_run):
    cfg = config.load_config(REPO / "config.toml", [
        f"paths.output_dir={json.dumps(example_run.parent.as_posix())}", "paths.run_name=test"])
    with pytest.raises(FileExistsError):
        pipeline.run(cfg, base=REPO)
