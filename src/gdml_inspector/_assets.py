"""Loading of the packaged browser assets (works from an installed wheel)."""

from __future__ import annotations

from importlib.resources import files


def read_asset(name: str) -> str:
    return files("gdml_inspector.assets").joinpath(name).read_text(encoding="utf-8")
