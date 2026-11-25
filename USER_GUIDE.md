# Virtual Roads – User Guide (version 2)

This guide explains how to set up, prepare data for, run and tune the Virtual Roads model.
For *what changed* compared with the 2021 scripts, see `virtual_roads_upgrade.txt`; for the method background see `README.md`.

**What the model does.** For every grid cell (default every 1 km) in your region that is not already on a road, it finds the least-cost off-road route a tractor-trailer could drive to the nearest existing road. Slopes steeper than a threshold (default 15°), water bodies and other obstacles are impassable. The result is one "virtual road" per origin cell, with its length, cost, climb and slope statistics. These are the virtual roads used by the BEA feasibility model (repository `BEA_Feasibility_model_test`).

---

## Contents
1. [Install](#1-install)
2. [Quick start with the example data](#2-quick-start-with-the-example-data)
3. [Input data: what you need and where to get it](#3-input-data-what-you-need-and-where-to-get-it)
4. [Earth projections (coordinate reference systems)](#4-earth-projections-coordinate-reference-systems)
5. [Preparing your own data, step by step](#5-preparing-your-own-data-step-by-step)
6. [Where to put the files](#6-where-to-put-the-files)
7. [Running the model](#7-running-the-model)
8. [What you can change: all settings](#8-what-you-can-change-all-settings)
9. [Outputs](#9-outputs)
10. [Using the results in the BEA model](#10-using-the-results-in-the-bea-model)
11. [Tests](#11-tests)
12. [Troubleshooting](#12-troubleshooting)

---

## 1. Install

You need Python 3.11 or newer with GDAL, geopandas, rasterio and scikit-image. On Windows, the easiest way is conda (Anaconda/Miniconda) with the conda-forge channel.

**Option A – new environment from the file in this repository**
```
conda env create -f environment.yml
conda activate virtual_roads
```

**Option B – an existing environment** (for example `mucp`)
```
conda activate mucp
conda install -c conda-forge gdal geopandas rasterio scikit-image pyproj pytest
```
`requirements.txt` lists the minimum versions. The model was tested with Python 3.12, GDAL 3.11, geopandas 1.1, shapely 2.1, rasterio 1.5 and scikit-image 0.26.

Check the installation:
```
python run_virtual_roads.py --print-config
python -m pytest -q
```

---

## 2. Quick start with the example data

The repository includes a small example region (about 15 x 15 km near Upington on the Orange River). From the repository folder:
```
python run_virtual_roads.py
```
After a few seconds the results are in `results/<date_time>/`. Open `results/<date_time>/virtual_roads.gpkg` in QGIS (layers `paths` and `origins`) together with `inputs/roads/NGI_2019_road_clip.shp`.

---

## 3. Input data: what you need and where to get it

| # | Input | Type | Required? | Setting (`config.toml`) |
|---|-------|------|-----------|--------------------------|
| 1 | Digital elevation model (DEM) | raster (GeoTIFF) | yes | `paths.dem` |
| 2 | Road network (the destinations) | lines (Shapefile/GeoPackage) | yes | `paths.roads` |
| 3 | Obstacles (rivers, dams, lakes, other no-go areas) | polygons and/or lines, a folder of files | recommended | `paths.obstacles_dir` |
| 4 | Equal-area grid template (BSU grid) | raster | yes (or make your own, section 5.4) | `paths.grid_template` |
| 5 | Region of interest (ROI) | polygon | recommended | `paths.roi` |

### 3.1 DEM – SRTM version 3, 1 arc-second (~30 m)
The model was designed for **SRTM v3 (SRTM Plus), 1 arc-second (~30 m)**, product name **SRTMGL1 v003**. It is free, but you need a free account.
* **NASA Earthdata Search**: <https://search.earthdata.nasa.gov> – search for `SRTMGL1`, draw your area, download the tiles (`.hgt` or GeoTIFF). Product page (DOI 10.5067/MEASURES/SRTM/SRTMGL1.003): <https://www.earthdata.nasa.gov/data/catalog/lpcloud-srtmgl1-003>.
* **USGS EarthExplorer**: <https://earthexplorer.usgs.gov> – Data Sets → Digital Elevation → SRTM → *SRTM 1 Arc-Second Global*.
* **OpenTopography**: <https://opentopography.org/dataCatalog> – *SRTM GL1 (30 m)*. It can clip and mosaic for you (free login).

Tiles are 1° x 1°. South Africa needs roughly 130 tiles; a district needs a few. Other DEMs (for example Copernicus DEM GLO-30 via <https://dataspace.copernicus.eu>, ALOS AW3D30, or a national 5 m/20 m DEM) work too. Any CRS and any cell size is fine, because the model reprojects the DEM itself. Set `surface.cell_size_m` close to the DEM's native resolution.

### 3.2 Roads – NGI topographic roads (or OpenStreetMap)
The example uses the **National Geo-spatial Information (NGI)** 2019 road layer from the 1:50 000 topographic vector data.
* NGI: <https://www.ngi.gov.za>. Vector data is available through the CD:NGI geospatial portal (<http://www.cdngiportal.co.za>, free registration) or on request from NGI.
* Alternative: **OpenStreetMap** roads, for example from Geofabrik (<https://download.geofabrik.de/africa/south-africa.html>, file `south-africa-latest-free.shp.zip`, layer `gis_osm_roads_free`).

Only the geometry is needed, so any attributes are fine. With `surface.road_filter` you can choose which roads count as destinations, for example `"FEAT_TYPE != 'Street'"` or, for OSM, `"fclass not in ['footway', 'path', 'track']"`.

### 3.3 Obstacles – water features and no-go areas
Anything a tractor cannot cross: rivers (as polygons, or lines for wide rivers), dams, lakes, wetlands, and optionally protected areas or urban areas.
* NGI 1:50 000 hydrographic features (same portal as the roads).
* Department of Water and Sanitation (DWS) river and dam layers: <https://www.dws.gov.za> (spatial data/GIS section).
* OpenStreetMap water polygons (Geofabrik `gis_osm_water_a_free`).

Put **every** obstacle file (Shapefile or GeoPackage) in one folder. All of them are burned in as barriers.

### 3.4 Equal-area grid template – the SANBI / Stats SA Basic Spatial Unit (BSU) grid
The BSU grid is a 100 m raster in the South African Albers equal-area projection (section 4). The model uses it for two things:
1. it provides the **analysis projection** (all routing is done in metres in this CRS);
2. it **aligns the origin grid**, so 1 km origin cells line up with the national BSU grid.

The cell *values* are not used. The BSU grid is distributed via SANBI (Biodiversity GIS, <https://bgis.sanbi.org>) and Stats SA. If you do not have it, make your own template in one command (section 5.4). Results are identical for the example.

### 3.5 Region of interest (ROI)
One polygon (Shapefile/GeoPackage) of the area you want virtual roads for, which you draw in QGIS. Routes are not allowed to leave the ROI (`surface.restrict_to_roi = true`). Use a slightly larger ROI than your study area so routes near the edge can reach roads just outside it. With `paths.roi = ""` the whole DEM is used.

---

## 4. Earth projections (coordinate reference systems)

| Data | Projection it may be in | Notes |
|------|------------------------|-------|
| DEM | any (SRTM comes in **WGS 84 geographic, EPSG:4326**) | reprojected automatically |
| Roads, obstacles, ROI | any, **but the CRS must be defined** (`.prj` file or GeoPackage CRS) | reprojected automatically |
| Grid template | **projected, in metres**. Recommended: **South African Albers equal area** (below) | defines the analysis CRS |
| Analysis (internal) | taken from the template, or set `surface.analysis_crs` | must be projected (metres) |
| Outputs | analysis CRS, plus WGS 84 copies (`*_wgs84` layers) | |

**South African Albers equal area** (used by the BSU grid, the BEA proximity rasters and this model):
```
+proj=aea +lat_1=-22 +lat_2=-38 +lat_0=-30 +lon_0=25 +x_0=1400000 +y_0=1300000 +datum=WGS84 +units=m +no_defs
```
It is equal-area and accurate across the whole country, so use it for national or multi-province runs.

Alternatives for a single region: UTM zones **33S–36S** (EPSG:32733 west coast, 32734, 32735, 32736 KZN/east). Set, for example, `surface.analysis_crs = "EPSG:32734"`. Do **not** use a geographic CRS (degrees) as the analysis CRS.

South African data is often in **Hartebeesthoek94** (EPSG:4148) or the "Lo" transverse Mercator system (for example `Hartebeesthoek94 / Lo29`, EPSG:2048). That is fine as long as the CRS is defined: Hartebeesthoek94 and WGS 84 differ by less than about 1 m.

**If a file has no CRS** (QGIS shows "unknown CRS", or there is no `.prj`), assign the correct one first. In QGIS: *Layer Properties → Source → Assigned CRS*. Or on the command line:
```
ogr2ogr -a_srs EPSG:4326 roads_fixed.gpkg roads.shp
```
(`-a_srs` *assigns* the CRS it really is in; `-t_srs` *transforms* to another CRS.)

---

## 5. Preparing your own data, step by step

All commands below are GDAL command-line tools. They are included with the conda GDAL package: open an *Anaconda Prompt*, `conda activate <env>`, then `cd` to your data folder. QGIS has the same tools under *Raster → Miscellaneous / Projections* and *Vector → Data Management Tools*.

### 5.1 DEM: mosaic the tiles and cut to your area
```
:: 1. make a virtual mosaic of all downloaded tiles (no copy, very fast)
gdalbuildvrt srtm_mosaic.vrt N*.hgt
::    (or *.tif depending on the download)

:: 2. cut to your ROI plus a margin and save as GeoTIFF
::    (roi_buffered.shp = your ROI buffered by ~2 km in QGIS: Vector > Geoprocessing > Buffer)
gdalwarp -cutline roi_buffered.shp -crop_to_cutline -of GTiff -co COMPRESS=DEFLATE srtm_mosaic.vrt dem_myarea.tif
```
The model reprojects the DEM itself, so there is no need to reproject it here. SRTM voids are rare in v3. If there are any, set `surface.dem_fill_max_distance_cells = 10`.

### 5.2 Roads: clip to the ROI (plus a margin) and check the CRS
```
ogr2ogr -clipsrc roi_buffered.shp roads_myarea.gpkg national_roads.shp
```
Make `roi_buffered.shp` in QGIS (*Vector → Geoprocessing → Buffer*, for example 2 km), so roads just outside the ROI are still destinations.

### 5.3 Obstacles: one folder, polygons preferred
```
mkdir inputs\my_obstacles
ogr2ogr -clipsrc roi_buffered.shp inputs\my_obstacles\water_polygons.gpkg national_water_areas.shp
ogr2ogr -clipsrc roi_buffered.shp inputs\my_obstacles\dams.gpkg national_dams.shp
```
Thin rivers drawn as lines are burned as 1-cell-wide barriers (`surface.obstacle_all_touched = true`). Only include line rivers that really are impassable, otherwise every stream becomes a wall.

### 5.4 Grid template (only if you do not have the BSU grid)
Create a 100 m Albers raster over your ROI. `-tap` aligns it to the national 100 m grid:
```
ogr2ogr -t_srs "+proj=aea +lat_1=-22 +lat_2=-38 +lat_0=-30 +lon_0=25 +x_0=1400000 +y_0=1300000 +datum=WGS84 +units=m +no_defs" roi_albers.gpkg roi.shp
gdal_rasterize -burn 1 -init 0 -ot Byte -tr 100 100 -tap roi_albers.gpkg grid_template.tif
```
(Tested: on the example this gives exactly the same results as the BSU grid.)

### 5.5 ROI
Draw a polygon in QGIS (*Layer → Create Layer → New GeoPackage Layer*, geometry type *Polygon*, CRS EPSG:4326 or Albers) and save it as `inputs/my_roi.gpkg`.

---

## 6. Where to put the files

The default layout, which you can change in `config.toml`:
```
Virtual_roads/
├── run_virtual_roads.py        <- the command you run
├── config.toml                 <- all settings (copy it for your own runs)
├── inputs/
│   ├── SRTM_clip.tif           <- DEM                     (paths.dem)
│   ├── BSU_100m_clip.tif       <- grid template           (paths.grid_template)
│   ├── ROI.shp (+ .dbf .prj .shx)  <- region of interest  (paths.roi)
│   ├── roads/NGI_2019_road_clip.shp <- roads              (paths.roads)
│   └── obstacles/              <- every .shp/.gpkg here is an obstacle (paths.obstacles_dir)
├── results/<run_name>/         <- created by each run (never overwritten)
├── vroads/                     <- model code
├── tests/                      <- automated tests
└── legacy/                     <- original 2021 scripts (reference only, do not run)
```
**Recommended for your own projects:** keep the code untouched and put each project in its own folder with its own settings file. Relative paths in a settings file are resolved from *that file's folder*:
```
D:\projects\karoo\
├── karoo.toml
├── data\dem_karoo.tif, roads_karoo.gpkg, roi_karoo.gpkg, grid_template.tif
└── data\obstacles\water.gpkg
```
In `karoo.toml`, write only what differs from the defaults:
```toml
[paths]
dem = "data/dem_karoo.tif"
roads = "data/roads_karoo.gpkg"
roi = "data/roi_karoo.gpkg"
grid_template = "data/grid_template.tif"
obstacles_dir = "data/obstacles"
output_dir = "results"
```
Run it with `python C:\path\to\Virtual_roads\run_virtual_roads.py --config D:\projects\karoo\karoo.toml`.

---

## 7. Running the model

Run from the repository folder (or give the full path to `run_virtual_roads.py`):

| Command | What it does |
|---------|--------------|
| `python run_virtual_roads.py` | run with `config.toml` (the example) |
| `python run_virtual_roads.py --config my.toml` | run with your settings file |
| `python run_virtual_roads.py --set paths.run_name=test1` | name the output folder `results/test1` |
| `python run_virtual_roads.py --set surface.max_slope_deg=12 --set surface.slope_weight=2` | change any setting for this run only |
| `python run_virtual_roads.py --config my.toml --set run.log_level=DEBUG` | combine a file with overrides |
| `python run_virtual_roads.py --print-config` | show the final settings without running |
| `python run_virtual_roads.py --help` | help |

Settings are applied in the order **built-in defaults → settings file → `--set`** (later wins).
`--set` values follow TOML rules: numbers `12`, booleans `true/false`, text `slope_only` (quotes optional unless it contains spaces or `=`), for example `--set "surface.road_filter=FEAT_TYPE != 'Street'"`.

A run never deletes or changes inputs, and it refuses to write into an existing run folder unless `run.overwrite = true`.

**Comparing scenarios.** Give each run a name:
```
python run_virtual_roads.py --set paths.run_name=base
python run_virtual_roads.py --set paths.run_name=slope12 --set surface.max_slope_deg=12
python run_virtual_roads.py --set paths.run_name=legacy_cost --set surface.cost_function=slope_only
```

**Runtime and memory.** The example takes about 6 s. Origins are routed in tiles (`routing.tile_size_m`, default 20 km), so memory stays bounded, but the DEM is warped in one piece. For a national run, split the country into provinces/regions (one ROI each) or use a coarser `surface.cell_size_m`.

---

## 8. What you can change: all settings

All settings live in `config.toml` with comments. The defaults are the values that were fixed in the original code (marked *orig.*).

### [paths]
| Setting | Default | Meaning |
|---------|---------|---------|
| `dem` | `inputs/SRTM_clip.tif` | DEM raster |
| `obstacles_dir` | `inputs/obstacles` | folder; every `.shp`/`.gpkg` inside is a barrier |
| `roads` | `inputs/roads/NGI_2019_road_clip.shp` | destination roads |
| `grid_template` | `inputs/BSU_100m_clip.tif` | equal-area template: analysis CRS + origin alignment |
| `roi` | `inputs/ROI.shp` | region of interest (`""` = whole DEM) |
| `output_dir` | `results` | parent folder of all runs |
| `run_name` | `""` | run folder name (`""` = date_time) |

### [surface] – the cost surface
| Setting | Default | Meaning / when to change |
|---------|---------|--------------------------|
| `analysis_crs` | `""` | `""` = template CRS. Or for example `"EPSG:32735"`. Must be metres. |
| `cell_size_m` | `30` | cost-surface resolution. Use the DEM resolution. Larger = faster and coarser. |
| `dem_resampling` | `bilinear` | `nearest`, `bilinear`, `cubic` when projecting the DEM |
| `dem_fill_max_distance_cells` | `0` | fill DEM voids up to this many cells (0 = off) |
| `max_slope_deg` | `15` *orig.* | steepest drivable slope; steeper cells are impassable. 10° is the legal limit for trucks in New Zealand; tractor-trailers manage about 15°. |
| `cost_function` | `distance_slope` | `distance_slope`: cost per metre = 1 + slope_weight x slope / max_slope. `slope_only` = *orig.* (cost = slope; flat ground is free, so routes may wander). |
| `slope_weight` | `1.0` | how strongly slope is penalised (2 = a 15° cell costs 3x flat ground) |
| `restrict_to_roi` | `true` *orig.* | routes stay inside the ROI |
| `roi_buffer_m` | `0` | extra margin processed around the ROI when `restrict_to_roi = false` |
| `obstacle_all_touched` | `true` | burn every cell an obstacle touches (safer for thin features) |
| `road_all_touched` | `true` | same for roads |
| `road_filter` | `""` | pandas query to choose destination roads, for example `"FEAT_TYPE != 'Street'"` |

### [points] – origins
| Setting | Default | Meaning |
|---------|---------|---------|
| `origin_spacing_m` | `1000` *orig.* | distance between origin points (a virtual road is made for each) |
| `snap_origin_radius_m` | `100` | origins inside water/steep cells move to the nearest passable cell within this distance; 0 = off (status `origin_impassable`) |

### [routing]
| Setting | Default | Meaning |
|---------|---------|---------|
| `tile_size_m` | `20000` | origins are processed in tiles of this size (memory control) |
| `tile_buffer_m` | `1000` *orig. ≈0.01°* | first search margin around a tile |
| `max_tile_buffer_m` | `16000` | the margin is doubled up to this when no road is found or a route hits the window edge. Increase it in very remote areas. |
| `fully_connected` | `true` *orig.* | 8-direction moves (false = 4 directions) |

### [output]
| Setting | Default | Meaning |
|---------|---------|---------|
| `gpkg_name` | `virtual_roads.gpkg` | output GeoPackage name |
| `write_wgs84` | `true` | also write `paths_wgs84`, `origins_wgs84` layers |
| `export_bea_vertices` | `true` | write the start/end vertex files for the BEA model |
| `keep_intermediate` | `true` | keep projected DEM, slope, cost and road rasters in `work/` |
| `simplify_tolerance_m` | `0` | simplify route lines (for example 10) to make files smaller |

### [run]
| Setting | Default | Meaning |
|---------|---------|---------|
| `log_level` | `INFO` | `DEBUG` for more detail |
| `overwrite` | `false` | allow writing into an existing run folder |

---

## 9. Outputs

Every run creates `results/<run_name>/`:

| File | Content |
|------|---------|
| `virtual_roads.gpkg` → `paths` | one line per origin (analysis CRS): the virtual road |
| `virtual_roads.gpkg` → `origins` | origin points with the same attributes |
| `virtual_roads.gpkg` → `paths_wgs84`, `origins_wgs84` | WGS 84 copies |
| `virtual_roads_summary.csv` | the attribute table as CSV |
| `bea_start_vertices.gpkg`, `bea_end_vertices.gpkg` | input for the BEA model (section 10) |
| `run.log` | the log of the run |
| `run_manifest.json` | settings used, library versions, input file checksums, timings, status counts |
| `work/` | `dem_projected.tif`, `slope_deg.tif`, `cost.tif` (-1 = impassable), `roads.tif` |

**Attributes per origin**
| Field | Meaning |
|-------|---------|
| `fid2`, `origin_id` | numeric id and grid id (`r<row>_c<col>` of the origin grid) |
| `status` | `ok` (route found), `on_road` (origin already on a road, length 0), `no_path` (no passable route to any road within the maximum window), `origin_impassable` (origin inside water/steep ground with no passable cell within the snap radius) |
| `length_m` | route length in metres |
| `cost` | accumulated cost (with `distance_slope`: "equivalent flat metres") |
| `climb_m`, `descent_m` | total uphill / downhill along the route |
| `max_slope_deg`, `mean_slope_deg` | slope statistics along the route |
| `snapped_m` | how far the origin was moved out of an obstacle |
| `edge_limited` | true if the route still touched the search window at its maximum size; it may not be optimal, so increase `routing.max_tile_buffer_m` |
| `buffer_m`, `tile` | search margin used and tile id |
| `origin_x/y`, `road_x/y` | start and end coordinates (analysis CRS) |

---

## 10. Using the results in the BEA model

The BEA feasibility model needs virtual-road start and end vertices. Point it at this model's output in the BEA settings file:
```toml
[paths]
vr_start_vertices = "C:/.../Virtual_roads/results/<run_name>/bea_start_vertices.gpkg"
vr_end_vertices   = "C:/.../Virtual_roads/results/<run_name>/bea_end_vertices.gpkg"
```
The BEA model also uses two 100 m proximity rasters (distance to roads, distance to VR start vertices). How to build them from these files is described in the BEA `USER_GUIDE.md`, section 5.4.

---

## 11. Tests

```
python -m pytest -q
```
There are 11 tests (about 5 s), covering settings handling, the cost function, routing on small synthetic grids (walls, unreachable pockets, snapping, adaptive search window, cheapest of two roads) and a full run of the example. Run the tests after changing code or installing a new environment.

---

## 12. Troubleshooting

| Problem | Cause / fix |
|---------|-------------|
| `FileNotFoundError: Input files not found` | a path in your settings is wrong. Relative paths are resolved from the settings file's folder. |
| `... has no CRS` | assign the CRS (section 4) |
| `Unknown config key` | typo in a setting name; `--print-config` shows valid names |
| `... already exists; choose another run_name` | use a new `paths.run_name` or `--set run.overwrite=true` |
| many `origin_impassable` | origins in water/steep ground: increase `points.snap_origin_radius_m`, or check the obstacle layers (line rivers burned as walls?) |
| many `no_path` | no road reachable inside the ROI: enlarge the ROI (include roads outside the study area), increase `routing.max_tile_buffer_m`, raise `surface.max_slope_deg`, or check that the road layer covers the area |
| `edge_limited = true` | increase `routing.max_tile_buffer_m` |
| out of memory | smaller ROI per run, larger `surface.cell_size_m`, smaller `routing.tile_size_m` |
| `ModuleNotFoundError: osgeo` / `skimage` | install GDAL / scikit-image into the active environment (section 1) |
| Old scripts (`legacy/`) fail | expected: they do not run on current libraries; use `run_virtual_roads.py` |

**About the links.** Earthdata, Geofabrik and DWS were reachable in November 2026. The South African government portals (NGI, CD:NGI, SANBI BGIS) could not be opened by an automated check at the time of writing (they may block automated access or have moved). If a link fails, search for the dataset name given here, or contact the provider (NGI: Department of Agriculture, Land Reform and Rural Development, Mowbray, Cape Town).
