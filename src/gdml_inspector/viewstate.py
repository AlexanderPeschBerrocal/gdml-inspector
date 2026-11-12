"""Loading a saved view state (``--view-state FILE``) for embedding at export.

A view state is the JSON the viewers' "Save view" button writes: camera,
filters, selection, clipping and theme, keyed by placement path rather than
numeric id. The viewers validate it completely before applying anything;
this module only refuses what could never be applied, with guidance, so a bad
file fails at export time instead of silently in the browser.
"""

from __future__ import annotations

import json
from pathlib import Path

VIEW_STATE_FORMAT = "gdml-inspector-view"
VIEW_STATE_VERSION = 1
_VIEWERS = ("standalone", "lazy")


def load_view_state(path: Path) -> dict[str, object]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read {path}: {exc.strerror or exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("format") != VIEW_STATE_FORMAT:
        raise ValueError(
            f"{path} is not a gdml-inspector view-state file (expected "
            f'"format": "{VIEW_STATE_FORMAT}"); create one with the viewer\'s '
            "Save view button."
        )
    version = raw.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ValueError(f"{path} has an invalid view-state version: {version!r}")
    if version > VIEW_STATE_VERSION:
        raise ValueError(
            f"{path} is view-state version {version}; this gdml-inspector reads "
            f"up to {VIEW_STATE_VERSION}. It was saved by a newer gdml-inspector: "
            "update it, or re-save the view with this version."
        )
    if raw.get("viewer") not in _VIEWERS:
        raise ValueError(
            f'{path}: "viewer" must be one of {_VIEWERS}, got {raw.get("viewer")!r}'
        )
    return raw


def stale_warning(state: dict[str, object], input_sha256: str) -> str | None:
    """A note when the view was saved for different geometry than is being
    exported; the viewer repeats it and lists any entries that no longer exist."""
    saved = state.get("savedFor")
    recorded = saved.get("inputSha256") if isinstance(saved, dict) else None
    if isinstance(recorded, str) and recorded != input_sha256:
        return (
            f"the view state was saved for different geometry (sha256 "
            f"{recorded[:12]}..., exporting {input_sha256[:12]}...); entries that "
            "no longer exist will be reported in the viewer"
        )
    return None
