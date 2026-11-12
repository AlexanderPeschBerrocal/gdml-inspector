"""HierarchyMixin: the world-to-placement tree the standalone viewer's
hierarchy panel is built from, including non-rendered container and
assembly nodes that add_volume() never sees."""

import json
import re

from gdml_inspector.hierarchy import HierarchyGDMLSceneBuilder
from gdml_inspector.standalone import write_html

from .helpers import DEFAULT_OPTIONS, hierarchy_nodes


def test_repeated_logical_volume_produces_two_distinct_branches(repeated_boxes):
    path = repeated_boxes(count=2)
    scene, nodes = hierarchy_nodes(path)
    first = nodes["world/copy_0"]
    second = nodes["world/copy_1"]
    assert first["rendered"] and second["rendered"]
    assert first["volume"] == second["volume"] == "leaf"
    assert first["objectId"] != second["objectId"]
    assert {first["objectId"], second["objectId"]} == {
        obj["id"] for obj in scene["objects"]
    }


def test_container_volume_is_present_but_not_rendered(nested_box):
    path = nested_box()
    _scene, nodes = hierarchy_nodes(path)
    container = nodes["world/parent"]
    assert container["volume"] == "group"
    assert container["rendered"] is False
    assert "objectId" not in container
    assert container["isAssembly"] is False

    leaf = nodes["world/parent/child"]
    assert leaf["volume"] == "leaf"
    assert leaf["rendered"] is True
    assert leaf["parentPath"] == "world/parent"


def test_world_root_has_no_parent(single_volume):
    path = single_volume()
    _scene, nodes = hierarchy_nodes(path)
    assert nodes["world"]["parentPath"] is None


def test_assembly_node_is_flagged_and_never_rendered(write_gdml):
    path = write_gdml(
        solids='<box name="shape" x="2" y="4" z="6" lunit="mm"/>',
        structure="""
<volume name="leaf"><materialref ref="G4_Si"/><solidref ref="shape"/></volume>
<assembly name="group"><physvol><volumeref ref="leaf"/>
  <position x="10" y="0" z="0" unit="mm"/>
</physvol></assembly>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol><volumeref ref="group"/></physvol>
</volume>""",
    )
    _scene, nodes = hierarchy_nodes(path)
    assembly_node = nodes["world/group"]
    assert assembly_node["isAssembly"] is True
    assert assembly_node["rendered"] is False
    assert "objectId" not in assembly_node

    leaf = nodes["world/group/leaf"]
    assert leaf["isAssembly"] is False
    assert leaf["rendered"] is True
    assert leaf["parentPath"] == "world/group"


def test_semantic_volume_strips_the_geant4_address_suffix(write_gdml):
    path = write_gdml(
        solids='<box name="shape" x="2" y="4" z="6" lunit="mm"/>',
        structure="""
<volume name="leaf_0x1a2b"><materialref ref="G4_Si"/><solidref ref="shape"/></volume>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol name="placed_0x3c4d"><volumeref ref="leaf_0x1a2b"/></physvol>
</volume>""",
    )
    _scene, nodes = hierarchy_nodes(path)
    node = nodes["world/placed_0x3c4d"]
    assert node["volume"] == "leaf_0x1a2b"
    assert node["semanticVolume"] == "leaf_"
    assert node["semanticName"] == "placed_"


def test_write_html_embeds_a_hierarchy_and_solids_table_matching_the_builder(
    tmp_path, single_volume
):
    path = single_volume()
    builder = HierarchyGDMLSceneBuilder(path, **DEFAULT_OPTIONS)
    scene = builder.build()
    nodes = builder.hierarchy_nodes()
    solids = builder.solids_table()

    output = tmp_path / "out.html"
    write_html(scene, nodes, solids, output)
    html = output.read_text(encoding="utf-8")

    hierarchy_match = re.search(r"const HIERARCHY = (.*?);\n", html)
    assert hierarchy_match is not None
    embedded = json.loads(hierarchy_match.group(1))
    assert embedded == nodes

    scene_match = re.search(r"const SCENE = (.*?);\n", html)
    assert scene_match is not None
    assert json.loads(scene_match.group(1)) == scene

    solids_match = re.search(r"const SOLIDS = (.*?);\n", html)
    assert solids_match is not None
    assert json.loads(solids_match.group(1)) == solids


def test_placement_local_and_global_matrices_are_captured(nested_box):
    path = nested_box()
    _scene, nodes = hierarchy_nodes(path)
    leaf = nodes["world/parent/child"]
    # "child" is placed at (5, 0, 0) mm inside "group", which is itself
    # placed at (10, 20, 30) mm inside world -- see conftest.py's fixture.
    assert leaf["localMatrix"][3] == 5
    assert leaf["localMatrix"][7] == 0
    assert leaf["localMatrix"][11] == 0
    assert leaf["globalMatrix"][3] == 15
    assert leaf["globalMatrix"][7] == 20
    assert leaf["globalMatrix"][11] == 30


def test_non_rendered_nodes_have_no_global_matrix(nested_box):
    path = nested_box()
    _scene, nodes = hierarchy_nodes(path)
    assert "globalMatrix" not in nodes["world"]
    assert "globalMatrix" not in nodes["world/parent"]
    assert "globalMatrix" in nodes["world/parent/child"]


def test_solids_table_exposes_declared_parameters_by_ref(single_volume):
    path = single_volume()
    builder = HierarchyGDMLSceneBuilder(path, **DEFAULT_OPTIONS)
    builder.parse()
    solids = builder.solids_table()
    assert solids["shape"] == {
        "type": "box",
        "params": {"x": 2.0, "y": 4.0, "z": 6.0},
    }


def test_solids_table_gives_a_boolean_solid_no_parameters_of_its_own(single_volume):
    path = single_volume("""
<box name="a" x="2" y="4" z="6" lunit="mm"/>
<box name="b" x="4" y="2" z="2" lunit="mm"/>
<union name="shape"><first ref="a"/><second ref="b"/>
  <position x="10" y="0" z="0" unit="mm"/>
</union>""")
    builder = HierarchyGDMLSceneBuilder(path, **DEFAULT_OPTIONS)
    builder.parse()
    solids = builder.solids_table()
    assert solids["shape"] == {"type": "union", "params": {}}
