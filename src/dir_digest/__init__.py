"""One content hash for a directory tree.

A cache key, a build fingerprint or a "has this folder changed?" question all need the
same thing: a digest that depends on the paths and the bytes, and on nothing else — not
the modification times, not the order the filesystem hands entries back, not the
separator the platform happens to use.

    >>> from dir_digest import digest_tree
    >>> digest_tree(".")               # doctest: +SKIP
    '9f2c…'

Ignore rules follow ``.gitignore``: ``*.pyc``, ``build/``, ``!keep.log`` and a leading
``/`` for anchored patterns.

Standard library only, Python 3.9+.
"""

from __future__ import annotations

import fnmatch  # noqa: F401  (kept for callers building patterns by hand)
import hashlib
import os
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

__all__ = ["IgnoreRules", "digest_tree", "file_digests"]

__version__ = "0.1.0"


def _translate(pattern: str) -> str:
    """Turn a gitignore glob into a regular expression body."""
    out: list[str] = []
    index = 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            out.append("(?:.*/)?")
            index += 3
        elif pattern.startswith("**", index):
            out.append(".*")
            index += 2
        elif pattern[index] == "*":
            out.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            out.append("[^/]")
            index += 1
        elif pattern[index] == "[":
            closing = pattern.find("]", index + 1)
            if closing == -1:
                out.append(re.escape("["))
                index += 1
            else:
                body = pattern[index + 1 : closing]
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append(f"[{body}]")
                index = closing + 1
        else:
            out.append(re.escape(pattern[index]))
            index += 1
    return "".join(out)


@dataclass(frozen=True)
class _Rule:
    regex: re.Pattern[str]
    negated: bool
    directory_only: bool

    def matches(self, relative: str, is_dir: bool) -> bool:
        if self.directory_only and not is_dir:
            return False
        return bool(self.regex.fullmatch(relative))


class IgnoreRules:
    """A set of gitignore-style patterns, last match winning."""

    def __init__(self, patterns: Iterable[str] = ()) -> None:
        self.rules: list[_Rule] = []
        for raw in patterns:
            line = raw.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            negated = line.startswith("!")
            if negated:
                line = line[1:]
            line = line.strip()
            if not line:
                continue
            directory_only = line.endswith("/")
            line = line.rstrip("/")
            anchored = line.startswith("/")
            line = line.lstrip("/")
            if not line:
                continue
            body = _translate(line)
            if "/" not in line and not anchored:
                body = f"(?:.*/)?{body}"
            self.rules.append(_Rule(re.compile(body), negated, directory_only))

    def __len__(self) -> int:
        return len(self.rules)

    def match(self, relative: str, is_dir: bool = False) -> bool:
        """True when the path is ignored."""
        ignored = False
        for rule in self.rules:
            if rule.matches(relative, is_dir):
                ignored = not rule.negated
        return ignored

    @classmethod
    def from_gitignore(cls, path: str | Path) -> IgnoreRules:
        """Read patterns from a file, ignoring it when it does not exist."""
        file = Path(path)
        if file.is_dir():
            file = file / ".gitignore"
        try:
            text = file.read_text(encoding="utf-8")
        except OSError:
            return cls()
        return cls(text.splitlines())


def _walk(
    root: Path,
    rules: IgnoreRules,
    follow_symlinks: bool,
) -> list[tuple[str, Path]]:
    """Collect ``(posix_relative_path, path)`` for the files that survive the rules."""
    kept: list[tuple[str, Path]] = []
    for current, directories, files in os.walk(root, followlinks=follow_symlinks):
        current_path = Path(current)
        relative_dir = current_path.relative_to(root).as_posix()
        prefix = "" if relative_dir == "." else f"{relative_dir}/"
        directories[:] = [
            name for name in sorted(directories) if not rules.match(f"{prefix}{name}", is_dir=True)
        ]
        for name in sorted(files):
            relative = f"{prefix}{name}"
            if rules.match(relative, is_dir=False):
                continue
            path = current_path / name
            if path.is_symlink() and not follow_symlinks:
                continue
            kept.append((relative, path))
    kept.sort(key=lambda item: item[0])
    return kept


def file_digests(
    root: str | Path,
    *,
    algorithm: str = "sha256",
    patterns: Sequence[str] = (),
    gitignore: bool = True,
    follow_symlinks: bool = False,
) -> dict[str, str]:
    """Digest every file under ``root``, keyed by its POSIX relative path."""
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        raise NotADirectoryError(f"not a directory: {root_path}")
    rules = IgnoreRules(patterns)
    if gitignore:
        rules = IgnoreRules([*_patterns_of(root_path), *patterns])
    digests: dict[str, str] = {}
    for relative, path in _walk(root_path, rules, follow_symlinks):
        hasher = hashlib.new(algorithm)
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                hasher.update(block)
        digests[relative] = hasher.hexdigest()
    return digests


def _patterns_of(root: Path) -> list[str]:
    file = root / ".gitignore"
    try:
        return file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


def digest_tree(
    root: str | Path,
    *,
    algorithm: str = "sha256",
    patterns: Sequence[str] = (),
    gitignore: bool = True,
    follow_symlinks: bool = False,
) -> str:
    """One digest for the whole tree, independent of order, mtimes and platform.

    Each file contributes its relative path and its content digest, separated by NUL,
    so renaming a file changes the result and two different trees cannot collide by
    concatenation.
    """
    digests = file_digests(
        root,
        algorithm=algorithm,
        patterns=patterns,
        gitignore=gitignore,
        follow_symlinks=follow_symlinks,
    )
    tree = hashlib.new(algorithm)
    for relative in sorted(digests):
        tree.update(relative.encode("utf-8"))
        tree.update(b"\0")
        tree.update(digests[relative].encode("ascii"))
        tree.update(b"\0")
    return tree.hexdigest()
