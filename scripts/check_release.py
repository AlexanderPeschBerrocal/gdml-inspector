#!/usr/bin/env python3
"""Validate that HEAD is a well-formed release commit. Read-only.

Usage: uv run --no-sync python scripts/check_release.py

Checks that the version increased, the tag matches pyproject.toml and
uv.lock, HEAD is exactly the "Release: {version}" commit touching only
pyproject.toml and uv.lock, the tag is annotated and points at HEAD, the
working tree is clean, and uv.lock agrees with pyproject.toml. Reports
every problem found, not just the first.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib as _toml
else:
    import tomli as _toml

ROOT = Path(__file__).resolve().parents[1]
TAG_PATTERN = re.compile(r"v\d+\.\d+\.\d+")


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def git_or_none(*args: str) -> str | None:
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def version_at(ref: str) -> str | None:
    text = git_or_none("show", f"{ref}:pyproject.toml")
    if text is None:
        return None
    data = _toml.loads(text)
    version = data.get("project", {}).get("version")
    return version if isinstance(version, str) else None


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def locked_version() -> str | None:
    with (ROOT / "uv.lock").open("rb") as handle:
        data = _toml.load(handle)
    for package in data.get("package", []):
        if package.get("name") == "gdml-inspector":
            version = package.get("version")
            return version if isinstance(version, str) else None
    return None


def main() -> int:
    problems: list[str] = []

    version = version_at("HEAD")
    if version is None:
        print(
            "check_release.py found problems:\n"
            "  - could not read pyproject.toml's version at HEAD",
            file=sys.stderr,
        )
        return 1

    previous = version_at("HEAD~1")
    if previous is None:
        problems.append("could not read pyproject.toml's version at HEAD~1")
    elif version_key(version) <= version_key(previous):
        problems.append(f"version {version} does not increase {previous}")

    tag = f"v{version}"
    if not TAG_PATTERN.fullmatch(tag):
        problems.append(f"{tag} is not a vX.Y.Z semver tag")

    locked = locked_version()
    if locked != version:
        problems.append(f"uv.lock records gdml-inspector {locked!r}, not {version!r}")

    if git("log", "-1", "--format=%s") != f"Release: {version}":
        problems.append(f'HEAD is not the release commit ("Release: {version}")')

    changed = set(git("diff", "--name-only", "HEAD~1", "HEAD").splitlines())
    if changed != {"pyproject.toml", "uv.lock"}:
        problems.append(
            f"release commit changes {sorted(changed)}, not exactly "
            "pyproject.toml and uv.lock"
        )

    tag_type = git_or_none("cat-file", "-t", tag)
    if tag_type != "tag":
        problems.append(f"{tag} is not an annotated tag (or does not exist)")
    else:
        tag_commit = git_or_none("rev-parse", f"{tag}^{{commit}}")
        if tag_commit != git("rev-parse", "HEAD"):
            problems.append(f"{tag} does not point to HEAD")

    if git("status", "--porcelain"):
        problems.append("working tree is not clean")

    if subprocess.run(["uv", "lock", "--check"], cwd=ROOT).returncode != 0:
        problems.append("uv.lock does not match pyproject.toml")

    if problems:
        print("check_release.py found problems:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print(f"HEAD is a valid release commit for {tag}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
