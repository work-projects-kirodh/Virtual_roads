"""Run the Virtual Roads model end to end in one command.

Examples
--------
    python run_virtual_roads.py                                  # defaults (= config.toml values)
    python run_virtual_roads.py --config my_area.toml
    python run_virtual_roads.py --set surface.max_slope_deg=12 --set paths.run_name=slope12
    python run_virtual_roads.py --print-config                   # show resolved settings
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from vroads import config, pipeline

REPO = Path(__file__).resolve().parent


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="TOML file with settings (default: config.toml if present)")
    ap.add_argument("--set", action="append", default=[], metavar="SECTION.KEY=VALUE",
                    help="override one setting; repeatable")
    ap.add_argument("--print-config", action="store_true", help="print the resolved config and exit")
    args = ap.parse_args(argv)

    cfg_file = args.config or (REPO / "config.toml" if (REPO / "config.toml").exists() else None)
    cfg = config.load_config(cfg_file, args.set)
    if args.print_config:
        print(config.dumps(cfg))
        return 0
    base = Path(args.config).resolve().parent if args.config else REPO
    run_dir = pipeline.run(cfg, base=base)
    print(f"Done. Results in {run_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
