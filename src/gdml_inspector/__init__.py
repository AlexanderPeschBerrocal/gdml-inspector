"""Browser-based inspection of GDML geometries.

Use the command-line entry points for a supported export workflow.
``gdml_inspector.api`` is the intended stable programmatic entry point for
reading a GDML scene from Python; see its module docstring for units,
identities, resource-budget semantics, error behaviour, and this package's
versioning policy (CHANGELOG.md).
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _distribution_version


def installed_version() -> str:
    """The installed distribution's version.

    ``pyproject.toml``'s ``version`` field is the one authoritative value
    (see CHANGELOG.md); this reads it back from the installed distribution
    rather than duplicating it as a second literal. Returns an
    intentionally invalid-looking sentinel, never a plausible-looking
    guess, if this package was imported without being installed at all (a
    bare source checkout with no ``pip install``/``uv sync``) -- every
    supported development workflow in this repo performs an editable
    install, so this fallback should not fire in ordinary use.
    """
    try:
        return _distribution_version("gdml-inspector")
    except PackageNotFoundError:
        return "0+unknown"


__version__ = installed_version()
