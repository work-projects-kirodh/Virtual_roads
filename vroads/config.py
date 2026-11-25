"""Configuration handling for the Virtual Roads model.

Every tunable value lives in DEFAULTS. The defaults reproduce the values that were
hard-coded in the original (2021) scripts wherever such a value existed. A user can
override any value in three ways, later ones winning:

1. DEFAULTS (this file)
2. a TOML file passed with ``--config`` (see ``config.toml`` in the repository root)
3. ``--set section.key=value`` on the command line (repeatable)
"""
from __future__ import annotations

import copy
import json
import tomllib
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, dict[str, Any]] = {
    "paths": {
        # Digital elevation model (any CRS). Original: inputs/SRTM_clip.tif
        "dem": "inputs/SRTM_clip.tif",
        # Folder with obstacle vector layers (all .shp/.gpkg inside are burned in as barriers)
        "obstacles_dir": "inputs/obstacles",
        # Road network = destinations of the virtual roads
        "roads": "inputs/roads/NGI_2019_road_clip.shp",
        # Equal-area template grid (BSU). Defines the analysis CRS and the origin grid alignment.
        "grid_template": "inputs/BSU_100m_clip.tif",
        # Region of interest polygon. Empty string = use the whole DEM extent.
        "roi": "inputs/ROI.shp",
        # Every run is written to <output_dir>/<run_name>/ ; nothing is ever written to inputs/
        "output_dir": "results",
        # Empty = timestamp (YYYYmmdd_HHMMSS)
        "run_name": "",
    },
    "surface": {
        # Analysis CRS. Empty = take it from grid_template (the BSU Albers equal-area grid).
        "analysis_crs": "",
        # Cell size of the cost surface in metres (SRTM v3 1-arc-second is ~30 m)
        "cell_size_m": 30.0,
        # Resampling used when the DEM is projected to the analysis grid
        "dem_resampling": "bilinear",
        # Fill small DEM voids before computing slope (0 = off). Search distance in cells.
        "dem_fill_max_distance_cells": 0,
        # Maximum navigable slope in degrees (original: 15). Cells >= this are impassable.
        "max_slope_deg": 15.0,
        # Cost model:
        #   "distance_slope" (new default): cost per metre = 1 + slope_weight * slope / max_slope
        #   "slope_only"     (original)   : cost per cell = slope in degrees (flat ground is free)
        "cost_function": "distance_slope",
        "slope_weight": 1.0,
        # Restrict routing to the ROI (the original clipped every window to the ROI)
        "restrict_to_roi": True,
        # Extra margin around the ROI that is still processed (only used if restrict_to_roi = false)
        "roi_buffer_m": 0.0,
        # Burn obstacles / roads using all touched cells
        "obstacle_all_touched": True,
        "road_all_touched": True,
        # Optional pandas query to select destination roads, e.g. "FEAT_TYPE != 'Street'"
        "road_filter": "",
    },
    "points": {
        # Spacing of the origin grid in metres (original: 1 km aggregate of the BSU grid)
        "origin_spacing_m": 1000.0,
        # Origins whose cell is impassable (water / steep) are moved to the nearest passable
        # cell within this radius. 0 disables snapping.
        "snap_origin_radius_m": 100.0,
    },
    "routing": {
        # Origins are processed in square tiles of this size (keeps memory bounded nationally)
        "tile_size_m": 20000.0,
        # Buffer around each tile (original: 0.01 degrees ~ 1 km)
        "tile_buffer_m": 1000.0,
        # If a path touches the window edge or no road is reachable, the buffer is doubled
        # until it reaches this maximum (removes window edge effects).
        "max_tile_buffer_m": 16000.0,
        # 8-connected moves (original: fully_connected=True)
        "fully_connected": True,
    },
    "output": {
        "gpkg_name": "virtual_roads.gpkg",
        # Also write WGS84 copies of the layers
        "write_wgs84": True,
        # Export start/end vertex layers in the format the BEA feasibility model reads
        # (Point geometries, fields fid2 and length in metres, EPSG:4326)
        "export_bea_vertices": True,
        # Keep the intermediate rasters (projected DEM, slope, cost, roads) in <run>/work
        "keep_intermediate": True,
        # Douglas-Peucker simplification of output paths in metres (0 = off)
        "simplify_tolerance_m": 0.0,
    },
    "run": {
        "log_level": "INFO",
        # Refuse to write into an existing run folder unless true
        "overwrite": False,
    },
}


class ConfigError(ValueError):
    """Raised for unknown keys or values of the wrong type."""


def _parse_value(text: str) -> Any:
    """Parse a command-line value using TOML rules, falling back to a plain string."""
    try:
        return tomllib.loads(f"v = {text}")["v"]
    except tomllib.TOMLDecodeError:
        return text


def _merge(base: dict, new: dict, where: str = "") -> None:
    for key, value in new.items():
        if key not in base:
            raise ConfigError(f"Unknown config key '{where}{key}'. Valid keys: {sorted(base)}")
        if isinstance(base[key], dict):
            if not isinstance(value, dict):
                raise ConfigError(f"'{where}{key}' must be a table/section")
            _merge(base[key], value, f"{where}{key}.")
        else:
            base[key] = _coerce(base[key], value, f"{where}{key}")


def _coerce(default: Any, value: Any, name: str) -> Any:
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in {"true", "false"}:
            return value.lower() == "true"
        raise ConfigError(f"'{name}' must be true/false, got {value!r}")
    if isinstance(default, (int, float)) and not isinstance(default, bool):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return type(default)(value) if isinstance(default, float) else value
        raise ConfigError(f"'{name}' must be a number, got {value!r}")
    if isinstance(default, str):
        return str(value)
    return value


def apply_override(cfg: dict, assignment: str) -> None:
    """Apply one ``section.key=value`` override in place."""
    if "=" not in assignment:
        raise ConfigError(f"Override '{assignment}' must look like section.key=value")
    dotted, raw = assignment.split("=", 1)
    parts = dotted.strip().split(".")
    if len(parts) != 2:
        raise ConfigError(f"Override key '{dotted}' must be section.key")
    _merge(cfg, {parts[0]: {parts[1]: _parse_value(raw.strip())}})


def load_config(path: str | Path | None = None, overrides: list[str] | None = None) -> dict:
    """Return the resolved configuration: DEFAULTS <- TOML file <- overrides."""
    cfg = copy.deepcopy(DEFAULTS)
    if path:
        with open(path, "rb") as fh:
            _merge(cfg, tomllib.load(fh))
    for item in overrides or []:
        apply_override(cfg, item)
    validate(cfg)
    return cfg


def validate(cfg: dict) -> None:
    s, r, p = cfg["surface"], cfg["routing"], cfg["points"]
    if s["cost_function"] not in {"distance_slope", "slope_only"}:
        raise ConfigError("surface.cost_function must be 'distance_slope' or 'slope_only'")
    for name, value in [("surface.cell_size_m", s["cell_size_m"]),
                        ("surface.max_slope_deg", s["max_slope_deg"]),
                        ("points.origin_spacing_m", p["origin_spacing_m"]),
                        ("routing.tile_size_m", r["tile_size_m"])]:
        if value <= 0:
            raise ConfigError(f"{name} must be > 0")
    if r["max_tile_buffer_m"] < r["tile_buffer_m"]:
        raise ConfigError("routing.max_tile_buffer_m must be >= routing.tile_buffer_m")


def dumps(cfg: dict) -> str:
    return json.dumps(cfg, indent=2)
