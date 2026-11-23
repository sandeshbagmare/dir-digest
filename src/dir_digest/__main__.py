"""Command line entry point: ``dir-digest`` or ``python -m dir_digest``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import digest_tree, file_digests


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dir-digest",
        description="One content hash for a directory tree.",
    )
    parser.add_argument("root", nargs="?", default=".", type=Path, metavar="DIR")
    parser.add_argument("--algorithm", default="sha256", help="hashlib algorithm (default sha256)")
    parser.add_argument(
        "--ignore",
        action="append",
        default=[],
        metavar="PATTERN",
        help="gitignore-style pattern, repeatable",
    )
    parser.add_argument(
        "--no-gitignore",
        action="store_true",
        help="do not read .gitignore from the directory",
    )
    parser.add_argument("--files", action="store_true", help="list each file's digest")
    parser.add_argument("--json", action="store_true", help="print JSON")
    args = parser.parse_args(argv)

    root = args.root
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2
    options = {
        "algorithm": args.algorithm,
        "patterns": args.ignore,
        "gitignore": not args.no_gitignore,
    }
    if args.algorithm not in ("sha256", "sha1", "sha512", "md5", "blake2b"):
        print(f"unsupported algorithm: {args.algorithm}", file=sys.stderr)
        return 2
    try:
        if args.files or args.json:
            digests = file_digests(root, **options)
            if args.json:
                print(json.dumps(digests, indent=2, sort_keys=True))
            else:
                for relative in sorted(digests):
                    print(f"{digests[relative]}  {relative}")
            return 0
        print(digest_tree(root, **options))
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
