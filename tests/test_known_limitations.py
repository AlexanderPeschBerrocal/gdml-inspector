"""Desired behaviour for tracked gaps, not passing snapshots of incorrect output.

Only assertion failures are expected: unrelated crashes remain failures.
Strict XPASS requires removing the marker when a linked issue fixes the gap.
"""

import pytest

from .helpers import scene_builder


@pytest.mark.xfail(
    raises=AssertionError, reason="#8: unknown length units are silently accepted"
)
def test_unknown_length_unit_has_an_actionable_diagnostic(single_volume):
    path = single_volume('<box name="shape" x="2" y="4" z="6" lunit="furlong"/>')
    try:
        scene = scene_builder(path).build()
    except ValueError as error:
        assert "unit" in str(error).lower()
    else:
        assert any("unit" in warning.lower() for warning in scene["meta"]["warnings"])
