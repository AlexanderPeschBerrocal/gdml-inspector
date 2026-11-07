"""Original, small GDML fixtures; no external detector files or native tools."""

from pathlib import Path

import pytest


@pytest.fixture
def example_gdml():
    return Path(__file__).resolve().parents[1] / "examples" / "detector.gdml"


@pytest.fixture
def write_gdml(tmp_path):
    sequence = 0

    def write(*, solids, structure, define="", world="world", namespace=""):
        nonlocal sequence
        sequence += 1
        path = tmp_path / f"fixture-{sequence}.gdml"
        ns = f' xmlns="{namespace}"' if namespace else ""
        path.write_text(
            f"""<?xml version="1.0"?>
<gdml{ns}>
  <define>{define}</define>
  <materials>
    <material name="Galactic" Z="1">
      <D value="1e-25" unit="g/cm3"/><atom value="1.01"/>
    </material>
    <material name="G4_Si" Z="14">
      <D value="2.33" unit="g/cm3"/><atom value="28.0855"/>
    </material>
  </materials>
  <solids>
    <box name="world_box" x="1000" y="1000" z="1000" lunit="mm"/>
    {solids}
  </solids>
  <structure>{structure}</structure>
  <setup name="Default" version="1.0"><world ref="{world}"/></setup>
</gdml>
""",
            encoding="utf-8",
        )
        return path

    return write


@pytest.fixture
def single_volume(write_gdml):
    def write(
        solid='<box name="shape" x="2" y="4" z="6" lunit="mm"/>',
        *,
        placement="",
        **options,
    ):
        return write_gdml(
            solids=solid,
            structure=f"""
<volume name="leaf"><materialref ref="G4_Si"/><solidref ref="shape"/></volume>
<volume name="world">
  <materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol name="placed"><volumeref ref="leaf"/>{placement}</physvol>
</volume>""",
            **options,
        )

    return write


@pytest.fixture
def repeated_boxes(write_gdml):
    def write(count=3, *, spacing=10):
        placements = "".join(
            f'<physvol name="copy_{i}"><volumeref ref="leaf"/>'
            f'<position x="{i * spacing}" y="0" z="0" unit="mm"/></physvol>'
            for i in range(count)
        )
        return write_gdml(
            solids='<box name="shape" x="2" y="4" z="6" lunit="mm"/>',
            structure=f"""
<volume name="leaf"><materialref ref="G4_Si"/><solidref ref="shape"/></volume>
<volume name="world"><materialref ref="Galactic"/>
  <solidref ref="world_box"/>{placements}
</volume>""",
        )

    return write


@pytest.fixture
def nested_box(write_gdml):
    def write(rotation_deg=0):
        return write_gdml(
            solids="""
<box name="shape" x="2" y="4" z="6" lunit="mm"/>
<box name="envelope" x="100" y="100" z="100" lunit="mm"/>""",
            structure=f"""
<volume name="leaf"><materialref ref="G4_Si"/><solidref ref="shape"/></volume>
<volume name="group"><materialref ref="Galactic"/><solidref ref="envelope"/>
  <physvol name="child"><volumeref ref="leaf"/>
    <position x="5" y="0" z="0" unit="mm"/>
  </physvol>
</volume>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol name="parent"><volumeref ref="group"/>
    <position x="10" y="20" z="30" unit="mm"/>
    <rotation x="0" y="0" z="{rotation_deg}" unit="deg"/>
  </physvol>
</volume>""",
        )

    return write
