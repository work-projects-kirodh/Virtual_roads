"""Build the analysis grid and the cost surface.

Fixes relative to the original scripts:
* everything is done in a projected, equal-area CRS (metres, square cells) instead of
  EPSG:4326 degrees (original slope used a fixed scale=111120, ignoring latitude);
* DEM NoData and the raster border are impassable instead of zero-cost corridors
  (original: ``slope[slope<0]=0``);
* the cost surface is returned explicitly (original ``assign_threshold`` returned the
  slope array by accident);
* obstacles/roads are rasterised in memory, nothing is written to inputs/.
"""
from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from osgeo import gdal
from rasterio.crs import CRS
from rasterio.features import rasterize
from rasterio.transform import Affine, from_origin

gdal.UseExceptions()
log = logging.getLogger(__name__)
NODATA = -9999.0


@dataclass
class Grid:
    """A north-up raster grid in the analysis CRS."""

    crs: CRS
    transform: Affine
    width: int
    height: int

    @property
    def cell(self) -> float:
        return self.transform.a

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        x0, y1 = self.transform.c, self.transform.f
        return x0, y1 - self.height * self.cell, x0 + self.width * self.cell, y1

    def rowcol(self, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        x0, y1 = self.transform.c, self.transform.f
        col = np.floor((np.asarray(x) - x0) / self.cell).astype(int)
        row = np.floor((y1 - np.asarray(y)) / self.cell).astype(int)
        return row, col

    def xy(self, row: np.ndarray, col: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        x0, y1 = self.transform.c, self.transform.f
        return x0 + (np.asarray(col) + 0.5) * self.cell, y1 - (np.asarray(row) + 0.5) * self.cell


@dataclass
class Surface:
    grid: Grid
    dem: np.ndarray        # metres, NaN where unknown
    slope: np.ndarray      # degrees, NaN where unknown
    cost: np.ndarray       # >0 finite = passable, inf = impassable
    roads: np.ndarray      # bool, destination cells (only passable ones)
    roi_mask: np.ndarray   # bool


def read_vector(path: str | os.PathLike, crs: CRS) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(path)
    if gdf.crs is None:
        raise ValueError(f"{path} has no CRS; define one before running the model")
    return gdf.to_crs(crs.to_wkt())


def analysis_crs(cfg: dict) -> CRS:
    if cfg["surface"]["analysis_crs"]:
        return CRS.from_user_input(cfg["surface"]["analysis_crs"])
    with rasterio.open(cfg["paths"]["grid_template"]) as src:
        return src.crs


def build_grid(cfg: dict, crs: CRS) -> tuple[Grid, gpd.GeoDataFrame | None]:
    """Grid covering the ROI (or the DEM), aligned to the template grid origin."""
    cell = cfg["surface"]["cell_size_m"]
    roi = None
    if cfg["paths"]["roi"]:
        roi = read_vector(cfg["paths"]["roi"], crs)
        xmin, ymin, xmax, ymax = roi.total_bounds
        pad = 0.0 if cfg["surface"]["restrict_to_roi"] else cfg["surface"]["roi_buffer_m"]
        xmin, ymin, xmax, ymax = xmin - pad, ymin - pad, xmax + pad, ymax + pad
    else:
        with rasterio.open(cfg["paths"]["dem"]) as src:
            from rasterio.warp import transform_bounds
            xmin, ymin, xmax, ymax = transform_bounds(src.crs, crs, *src.bounds)
    ax, ay = 0.0, 0.0
    if cfg["paths"]["grid_template"]:
        with rasterio.open(cfg["paths"]["grid_template"]) as src:
            ax, ay = src.transform.c, src.transform.f
    xmin = ax + math.floor((xmin - ax) / cell) * cell
    ymax = ay + math.ceil((ymax - ay) / cell) * cell
    width = int(math.ceil((xmax - xmin) / cell))
    height = int(math.ceil((ymax - ymin) / cell))
    return Grid(crs, from_origin(xmin, ymax, cell, cell), width, height), roi


def _warp_dem(cfg: dict, grid: Grid, out_path: str) -> np.ndarray:
    xmin, ymin, xmax, ymax = grid.bounds
    ds = gdal.Warp(out_path, cfg["paths"]["dem"], format="GTiff", dstSRS=grid.crs.to_wkt(),
                   outputBounds=(xmin, ymin, xmax, ymax), xRes=grid.cell, yRes=grid.cell,
                   resampleAlg=cfg["surface"]["dem_resampling"], dstNodata=NODATA,
                   outputType=gdal.GDT_Float32)
    fill = cfg["surface"]["dem_fill_max_distance_cells"]
    if fill > 0:
        band = ds.GetRasterBand(1)
        gdal.FillNodata(band, None, maxSearchDist=fill, smoothingIterations=0)
        band.FlushCache()
    ds = None
    with rasterio.open(out_path) as src:
        dem = src.read(1).astype("float64")
    dem[dem == NODATA] = np.nan
    return dem


def _slope(dem_path: str, out_path: str) -> np.ndarray:
    gdal.DEMProcessing(out_path, dem_path, "slope", alg="Horn", slopeFormat="degree",
                       computeEdges=True, format="GTiff")
    with rasterio.open(out_path) as src:
        slope = src.read(1).astype("float64")
        nd = src.nodata
    if nd is not None:
        slope[slope == nd] = np.nan
    return slope


def _burn(geoms, grid: Grid, all_touched: bool) -> np.ndarray:
    geoms = [g for g in geoms if g is not None and not g.is_empty]
    if not geoms:
        return np.zeros((grid.height, grid.width), dtype=bool)
    return rasterize(((g, 1) for g in geoms), out_shape=(grid.height, grid.width),
                     transform=grid.transform, fill=0, all_touched=all_touched,
                     dtype="uint8").astype(bool)


def obstacle_files(folder: str) -> list[Path]:
    if not folder or not os.path.isdir(folder):
        return []
    return sorted(p for p in Path(folder).rglob("*") if p.suffix.lower() in {".shp", ".gpkg"})


def cost_from_slope(slope: np.ndarray, impassable: np.ndarray, cfg: dict) -> np.ndarray:
    s = cfg["surface"]
    if s["cost_function"] == "slope_only":
        cost = np.where(np.isnan(slope), np.inf, slope)
    else:
        cost = 1.0 + s["slope_weight"] * np.nan_to_num(slope) / s["max_slope_deg"]
    cost = cost.astype("float64")
    cost[impassable] = np.inf
    return cost


def build_surface(cfg: dict, work_dir: Path) -> Surface:
    crs = analysis_crs(cfg)
    grid, roi = build_grid(cfg, crs)
    log.info("Analysis grid: %d x %d cells of %.1f m (%s)", grid.width, grid.height,
             grid.cell, crs.to_string()[:60])
    dem_path, slope_path = str(work_dir / "dem_projected.tif"), str(work_dir / "slope_deg.tif")
    dem = _warp_dem(cfg, grid, dem_path)
    slope = _slope(dem_path, slope_path)
    slope[np.isnan(dem)] = np.nan

    s = cfg["surface"]
    roi_mask = (_burn(roi.geometry, grid, all_touched=False) if roi is not None
                else np.ones((grid.height, grid.width), dtype=bool))
    obstacles = np.zeros_like(roi_mask)
    for f in obstacle_files(cfg["paths"]["obstacles_dir"]):
        obstacles |= _burn(read_vector(f, crs).geometry, grid, s["obstacle_all_touched"])
        log.info("Obstacle layer burned: %s", f.name)

    roads_gdf = read_vector(cfg["paths"]["roads"], crs)
    if s["road_filter"]:
        roads_gdf = roads_gdf.query(s["road_filter"])
    roads = _burn(roads_gdf.geometry, grid, s["road_all_touched"])

    steep = slope >= s["max_slope_deg"]
    impassable = np.isnan(slope) | steep | obstacles
    if s["restrict_to_roi"]:
        impassable |= ~roi_mask
    cost = cost_from_slope(slope, impassable, cfg)
    roads &= np.isfinite(cost)
    log.info("Cost surface: %.1f%% impassable (steep %.1f%%, obstacles %.1f%%), %d road cells",
             100 * impassable.mean(), 100 * steep.mean(), 100 * obstacles.mean(), roads.sum())

    if cfg["output"]["keep_intermediate"]:
        profile = dict(driver="GTiff", width=grid.width, height=grid.height, count=1,
                       crs=grid.crs, transform=grid.transform, compress="deflate")
        with rasterio.open(work_dir / "cost.tif", "w", dtype="float32", nodata=-1, **profile) as dst:
            dst.write(np.where(np.isfinite(cost), cost, -1).astype("float32"), 1)
        with rasterio.open(work_dir / "roads.tif", "w", dtype="uint8", nodata=0, **profile) as dst:
            dst.write(roads.astype("uint8"), 1)
    else:
        for p in (dem_path, slope_path):
            Path(p).unlink(missing_ok=True)
    return Surface(grid, dem, slope, cost, roads, roi_mask)
