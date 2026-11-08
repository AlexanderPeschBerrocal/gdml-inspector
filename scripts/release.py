#!/usr/bin/env python3
"""Prepare a release commit and annotated tag locally. Never pushes.

Usage: uv run --no-sync python scripts/release.py {major|minor|patch}

Checks the branch and working tree, bumps pyproject.toml's version via
bump-my-version, regenerates uv.lock, and commits exactly those two files
as "Release: {version}" with an annotated "v{version}" tag. Review the
result (and run check_release.py) before pushing; see docs/releasing.md
for the full process, including adding a CHANGELOG.md entry first.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib as _toml
else:
    import tomli as _toml

ROOT = Path(__file__).resolve().parents[1]


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def git_or_none(*args: str) -> str | None:
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def run(*args: str) -> None:
    subprocess.run(args, cwd=ROOT, check=True)


def project_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        version = _toml.load(handle)["project"]["version"]
    if not isinstance(version, str):
        raise TypeError(
            f"pyproject.toml's project.version is not a string: {version!r}"
        )
    return version


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("part", choices=["major", "minor", "patch"])
    args = parser.parse_args(argv)

    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    if branch != "main":
        parser.error(f"must be run from main, not {branch!r}")
    if git("status", "--porcelain"):
        parser.error("working tree is not clean")

    old_version = project_version()
    run("bump-my-version", "bump", args.part)
    new_version = project_version()

    # Everything from here on must leave a clean working tree (no commit, no
    # tag) on any failure: bump-my-version already wrote pyproject.toml.
    def abort(message: str) -> None:
        git("checkout", "--", "pyproject.toml")
        parser.error(message)

    if version_key(new_version) <= version_key(old_version):
        abort(
            f"bumping {args.part!r} produced {new_version}, which does not "
            f"increase {old_version}"
        )

    tag = f"v{new_version}"
    if git_or_none("rev-parse", "--verify", "--quiet", tag) is not None:
        abort(
            f"tag {tag} already exists -- delete it first if this release was "
            "already prepared, or check why this version is already used"
        )

    run("uv", "lock")
    git("add", "pyproject.toml", "uv.lock")
    git("commit", "-m", f"Release: {new_version}")
    git("tag", "-a", tag, "-m", f"gdml-inspector {new_version}")

    print(f"Prepared release {new_version} locally. Review it, then run:")
    print(f"  git push --atomic origin HEAD:refs/heads/main refs/tags/{tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
