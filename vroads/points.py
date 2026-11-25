"""Origin points (the cells that need a virtual road).

Fix: the original driver read ``outputs/1000m_source.shp`` but never created it, so a
fresh checkout crashed. Origins are now generated here every run: the centres of a
regular grid (default 1 km) aligned to the BSU template grid and clipped to the ROI.
"""
from __future__ import annotations

import math

import geopandas as gpd
import numpy as np
import rasterio
from shapely import contains_xy

from .surface import Grid


def make_origins(cfg: dict, grid: Grid, roi: gpd.GeoDataFrame | None) -> gpd.GeoDataFrame:
    spacing = cfg["points"]["origin_spacing_m"]
    ax, ay = grid.transform.c, grid.transform.f
    if cfg["paths"]["grid_template"]:
        with rasterio.open(cfg["paths"]["grid_template"]) as src:
            ax, ay = src.transform.c, src.transform.f
    xmin, ymin, xmax, ymax = grid.bounds
    i0, i1 = math.floor((xmin - ax) / spacing), math.ceil((xmax - ax) / spacing)
    j0, j1 = math.floor((ay - ymax) / spacing), math.ceil((ay - ymin) / spacing)
    ii, jj = np.meshgrid(np.arange(i0, i1), np.arange(j0, j1))
    x = ax + (ii.ravel() + 0.5) * spacing
    y = ay - (jj.ravel() + 0.5) * spacing
    keep = (x >= xmin) & (x < xmax) & (y > ymin) & (y <= ymax)
    if roi is not None:
        keep &= contains_xy(roi.union_all(), x, y)
    gdf = gpd.GeoDataFrame(
        {"origin_id": [f"r{j}_c{i}" for j, i in zip(jj.ravel()[keep], ii.ravel()[keep])]},
        geometry=gpd.points_from_xy(x[keep], y[keep]), crs=grid.crs.to_wkt())
    gdf.insert(0, "fid2", np.arange(len(gdf), dtype=int))
    return gdf
