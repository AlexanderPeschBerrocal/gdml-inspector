"""Loading of the packaged browser assets (works from an installed wheel)."""

from __future__ import annotations

import re
from importlib.resources import files


def read_asset(name: str) -> str:
    return files("gdml_inspector.assets").joinpath(name).read_text(encoding="utf-8")


def render_template(name: str, tokens: dict[str, str]) -> str:
    """Fill ``__TOKEN__`` placeholders in a packaged HTML template in ONE pass,
    so text inserted for one token (GDML-derived JSON can contain anything,
    including the literal ``__SCENE__``) is never rescanned for the others."""
    pattern = re.compile("|".join(re.escape(f"__{token}__") for token in tokens))
    return pattern.sub(lambda match: tokens[match.group(0)[2:-2]], read_asset(name))
