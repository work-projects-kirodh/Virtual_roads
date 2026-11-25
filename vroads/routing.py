"""Least-cost routing from every origin to the nearest (cheapest) road cell.

Algorithm (replaces the original first_run/second_run):
* origins are grouped into square tiles; each tile is routed inside a window
  (tile + buffer) cut from the cost surface;
* ONE multi-source minimum-cost-path run per window, seeded from all road cells, gives
  the cheapest cost from every cell to the road network; each origin is then a cheap
  traceback (the original ran one MCP per origin and rasterised the roads per origin);
* if an origin cannot reach a road, or its path touches the window edge (a possible edge
  effect), the window buffer is doubled and the origin re-routed, up to
  ``routing.max_tile_buffer_m``;
* every origin gets an explicit status instead of being silently skipped
  ("ok", "on_road", "no_path", "origin_impassable").
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import numpy as np
from skimage.graph import MCP_Geometric

from .surface import Surface

log = logging.getLogger(__name__)


@dataclass
class Route:
    fid2: int
    origin_id: str
    status: str
    cells: list[tuple[int, int]] = field(default_factory=list)   # origin -> road, global rows/cols
    cost: float = math.nan
    length_m: float = math.nan
    climb_m: float = math.nan
    descent_m: float = math.nan
    max_slope_deg: float = math.nan
    mean_slope_deg: float = math.nan
    snapped_m: float = 0.0
    buffer_m: float = math.nan
    edge_limited: bool = False
    tile: str = ""


def _metrics(route: Route, surface: Surface) -> None:
    cells = np.asarray(route.cells)
    steps = np.hypot(*np.diff(cells, axis=0).T) if len(cells) > 1 else np.zeros(0)
    route.length_m = float(steps.sum() * surface.grid.cell)
    z = surface.dem[cells[:, 0], cells[:, 1]]
    dz = np.diff(z)
    route.climb_m = float(np.nansum(np.clip(dz, 0, None)))
    route.descent_m = float(np.nansum(np.clip(-dz, 0, None)))
    s = surface.slope[cells[:, 0], cells[:, 1]]
    route.max_slope_deg = float(np.nanmax(s)) if np.isfinite(s).any() else math.nan
    route.mean_slope_deg = float(np.nanmean(s)) if np.isfinite(s).any() else math.nan


def _snap(passable: np.ndarray, r: int, c: int, radius_cells: int) -> tuple[int, int] | None:
    """Nearest passable cell within radius (None if none)."""
    if radius_cells <= 0:
        return None
    r0, r1 = max(r - radius_cells, 0), min(r + radius_cells + 1, passable.shape[0])
    c0, c1 = max(c - radius_cells, 0), min(c + radius_cells + 1, passable.shape[1])
    rr, cc = np.nonzero(passable[r0:r1, c0:c1])
    if rr.size == 0:
        return None
    d = np.hypot(rr + r0 - r, cc + c0 - c)
    ok = d <= radius_cells
    if not ok.any():
        return None
    k = np.argmin(np.where(ok, d, np.inf))
    return int(rr[k] + r0), int(cc[k] + c0)


def route_all(surface: Surface, origins, cfg: dict) -> list[Route]:
    grid, rcfg = surface.grid, cfg["routing"]
    cell = grid.cell
    xs, ys = origins.geometry.x.to_numpy(), origins.geometry.y.to_numpy()
    rows, cols = grid.rowcol(xs, ys)
    tile_cells = max(1, int(round(rcfg["tile_size_m"] / cell)))
    snap_cells = int(cfg["points"]["snap_origin_radius_m"] // cell)

    results: dict[int, Route] = {}
    tiles: dict[tuple[int, int], list[int]] = {}
    for k, (r, c) in enumerate(zip(rows, cols)):
        tiles.setdefault((r // tile_cells, c // tile_cells), []).append(k)
    log.info("Routing %d origins in %d tile(s)", len(origins), len(tiles))

    for (tr, tc), members in sorted(tiles.items()):
        pending = members
        buffer_m = rcfg["tile_buffer_m"]
        while pending:
            at_max = buffer_m >= rcfg["max_tile_buffer_m"]
            b = int(math.ceil(buffer_m / cell))
            r0 = max(min(rows[k] for k in pending) - b, 0)
            c0 = max(min(cols[k] for k in pending) - b, 0)
            r1 = min(max(rows[k] for k in pending) + b + 1, grid.height)
            c1 = min(max(cols[k] for k in pending) + b + 1, grid.width)
            sub_cost = surface.cost[r0:r1, c0:c1]
            starts = np.argwhere(surface.roads[r0:r1, c0:c1])
            retry: list[int] = []
            if len(starts) == 0:
                costs = np.full(sub_cost.shape, np.inf)
                mcp = None
            else:
                mcp = MCP_Geometric(sub_cost, sampling=(cell, cell),
                                    fully_connected=rcfg["fully_connected"])
                costs, _ = mcp.find_costs(starts.tolist())
            edge_top, edge_left = r0 > 0, c0 > 0
            edge_bottom, edge_right = r1 < grid.height, c1 < grid.width
            for k in pending:
                o = origins.iloc[k]
                route = Route(int(o.fid2), o.origin_id, "no_path", buffer_m=buffer_m,
                              tile=f"{tr}_{tc}")
                lr, lc = int(rows[k] - r0), int(cols[k] - c0)
                inside = 0 <= rows[k] < grid.height and 0 <= cols[k] < grid.width
                if not inside:
                    route.status = "origin_impassable"
                    results[k] = route
                    continue
                if not np.isfinite(sub_cost[lr, lc]):
                    snapped = _snap(np.isfinite(sub_cost), lr, lc, snap_cells)
                    if snapped is None:
                        # an origin inside an obstacle does not improve with a wider window
                        route.status = "origin_impassable"
                        results[k] = route
                        continue
                    route.snapped_m = float(np.hypot(snapped[0] - lr, snapped[1] - lc) * cell)
                    lr, lc = snapped
                if mcp is None or not np.isfinite(costs[lr, lc]):
                    if not at_max:
                        retry.append(k)
                    else:
                        results[k] = route
                    continue
                path = mcp.traceback((lr, lc))[::-1]          # origin -> road
                pr = np.array([p[0] for p in path])
                pc = np.array([p[1] for p in path])
                touches = ((edge_top and (pr == 0).any()) or (edge_left and (pc == 0).any())
                           or (edge_bottom and (pr == sub_cost.shape[0] - 1).any())
                           or (edge_right and (pc == sub_cost.shape[1] - 1).any()))
                if touches and not at_max:
                    retry.append(k)
                    continue
                route.edge_limited = bool(touches)
                route.cells = [(int(a + r0), int(b_ + c0)) for a, b_ in zip(pr, pc)]
                route.cost = float(costs[lr, lc])
                route.status = "on_road" if len(route.cells) == 1 else "ok"
                _metrics(route, surface)
                results[k] = route
            if retry:
                log.debug("Tile %s_%s: %d origin(s) re-routed with a %.0f m buffer",
                          tr, tc, len(retry), min(buffer_m * 2, rcfg["max_tile_buffer_m"]))
            pending = retry
            buffer_m = min(buffer_m * 2, rcfg["max_tile_buffer_m"])
    return [results[k] for k in range(len(origins))]
