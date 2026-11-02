#!/usr/bin/env python3
"""starter-kit CLI. Subcommand logic lives in starterkit/<command>.py."""

import argparse
import sys
from pathlib import Path

from starterkit import apply as apply_cmd
from starterkit import update as update_cmd

KIT_ROOT = Path(__file__).resolve().parent


def main(argv=None):
    parser = argparse.ArgumentParser(prog="kit.py")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("apply", help="apply layers to a git project")
    p.add_argument("--layer", action="append", default=[], help="layer name (repeatable); base is always applied")
    p.add_argument("--layers-dir", type=Path, default=KIT_ROOT / "layers", help=argparse.SUPPRESS)
    p.add_argument("target", type=Path, help="project directory (a git repository)")

    p = sub.add_parser("update", help="update a project to the current kit, keeping its edits")
    p.add_argument("--kit-root", type=Path, default=KIT_ROOT, help=argparse.SUPPRESS)
    p.add_argument("--layers-dir", type=Path, default=None, help=argparse.SUPPRESS)
    p.add_argument("target", type=Path, help="project directory previously set up with apply")

    args = parser.parse_args(argv)
    try:
        if args.command == "apply":
            apply_cmd.run(KIT_ROOT, args.layers_dir, args.layer, args.target)
        elif args.command == "update":
            update_cmd.run(args.kit_root, args.layers_dir or args.kit_root / "layers", args.target)
    except apply_cmd.KitError as e:
        print(f"kit: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
