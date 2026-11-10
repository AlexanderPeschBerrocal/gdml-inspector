"""Small GDML inputs checked against analytic bounds and explicit diagnostics."""

import pytest

from .helpers import lazy_builder, scene_builder


def test_example_counts_buffers_materials_and_endcap_positions(example_gdml):
    scene = scene_builder(example_gdml).build()
    assert scene["meta"]["objects"] == 5
    assert scene["meta"]["warnings"] == []
    assert scene["meta"]["materials"] == {"G4_Si": 4, "G4_Fe": 1}
    assert len(scene["positions"]) == scene["meta"]["vertices"] * 3
    assert len(scene["colors"]) == scene["meta"]["vertices"] * 4
    assert len(scene["ids"]) == scene["meta"]["vertices"]
    endcaps = [o for o in scene["objects"] if o["volume"] == "pixel_endcap"]
    assert sorted((o["bbox"][2], o["bbox"][5]) for o in endcaps) == [
        (-351.5, -348.5),
        (348.5, 351.5),
    ]


def test_repeated_logical_volume_has_distinct_placements_and_ids(repeated_boxes):
    scene = scene_builder(repeated_boxes(2)).build()
    assert [o["volume"] for o in scene["objects"]] == ["leaf", "leaf"]
    assert [o["path"] for o in scene["objects"]] == ["world/copy_0", "world/copy_1"]
    expected_bounds = [[-1, -2, -3, 1, 2, 3], [9, -2, -3, 11, 2, 3]]
    for index, (obj, expected) in enumerate(
        zip(scene["objects"], expected_bounds, strict=True)
    ):
        assert obj["id"] == index
        assert obj["bbox"] == expected
        start = obj["firstVertex"]
        stop = start + obj["vertexCount"]
        assert obj["vertexCount"] == 36  # six faces, two triangles, three vertices
        assert scene["ids"][start:stop] == [index] * 36
    assert scene["meta"]["bbox"] == [-1, -2, -3, 11, 2, 3]


def test_xml_namespaces_preserve_solid_and_volume_lookup(single_volume):
    scene = scene_builder(single_volume(namespace="urn:gdml:fixture")).build()
    assert scene["meta"]["warnings"] == []
    assert scene["objects"][0]["bbox"] == [-1, -2, -3, 1, 2, 3]


def test_container_and_world_geometry_are_opt_in(nested_box):
    path = nested_box()
    leaf_only = scene_builder(path).build()
    with_containers = scene_builder(path, include_containers=True).build()
    with_world = scene_builder(
        path, include_containers=True, include_world=True
    ).build()
    assert [o["volume"] for o in leaf_only["objects"]] == ["leaf"]
    assert [o["volume"] for o in with_containers["objects"]] == ["group", "leaf"]
    assert with_containers["meta"]["bbox"] == [-40, -30, -20, 60, 70, 80]
    assert [o["volume"] for o in with_world["objects"]] == ["world", "group", "leaf"]
    assert with_world["meta"]["bbox"] == [-500, -500, -500, 500, 500, 500]


@pytest.mark.parametrize("section", ["<solids/>", "<structure/>"])
def test_missing_required_sections_raise_an_actionable_error(tmp_path, section):
    path = tmp_path / "missing-section.gdml"
    path.write_text(f"<gdml>{section}</gdml>")
    with pytest.raises(ValueError, match="sections"):
        scene_builder(path).build()


def test_missing_world_reference_is_reported(single_volume):
    with pytest.raises(ValueError, match="absent"):
        scene_builder(single_volume(world="absent")).build()


def test_missing_solid_has_a_warning_and_finite_empty_bounds(single_volume):
    scene = scene_builder(
        single_volume('<box name="other" x="2" y="4" z="6"/>')
    ).build()
    assert scene["meta"]["objects"] == 0
    assert scene["positions"] == []
    assert scene["meta"]["bbox"] == [-1, -1, -1, 1, 1, 1]
    assert any("Missing solid reference: shape" in w for w in scene["meta"]["warnings"])


def test_duplicate_volume_definition_is_reported(write_gdml):
    path = write_gdml(
        solids='<box name="shape" x="2" y="4" z="6" lunit="mm"/>',
        structure="""
<volume name="leaf"><materialref ref="G4_Si"/><solidref ref="shape"/></volume>
<volume name="leaf"><materialref ref="G4_Si"/><solidref ref="shape"/></volume>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol><volumeref ref="leaf"/></physvol>
</volume>""",
    )
    scene = scene_builder(path).build()
    assert any(
        "Duplicate GDML volume definition: leaf" in w for w in scene["meta"]["warnings"]
    )


def test_duplicate_solid_definition_is_reported(single_volume):
    path = single_volume(
        '<box name="shape" x="2" y="4" z="6" lunit="mm"/>'
        '<box name="shape" x="1" y="1" z="1" lunit="mm"/>'
    )
    scene = scene_builder(path).build()
    assert any(
        "Duplicate GDML solid definition: shape" in w for w in scene["meta"]["warnings"]
    )


def test_duplicate_assembly_definition_is_reported(write_gdml):
    path = write_gdml(
        solids='<box name="shape" x="2" y="4" z="6" lunit="mm"/>',
        structure="""
<volume name="leaf"><materialref ref="G4_Si"/><solidref ref="shape"/></volume>
<assembly name="group"><physvol><volumeref ref="leaf"/></physvol></assembly>
<assembly name="group"><physvol><volumeref ref="leaf"/></physvol></assembly>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol><volumeref ref="group"/></physvol>
</volume>""",
    )
    scene = scene_builder(path).build()
    assert any(
        "Duplicate GDML assembly definition: group" in w
        for w in scene["meta"]["warnings"]
    )


def test_duplicate_named_position_definition_is_reported(single_volume):
    path = single_volume(
        placement='<positionref ref="shift"/>',
        define="""
<position name="shift" x="10" y="20" z="30" unit="mm"/>
<position name="shift" x="1" y="1" z="1" unit="mm"/>""",
    )
    scene = scene_builder(path).build()
    assert any(
        "Duplicate GDML position definition: shift" in w
        for w in scene["meta"]["warnings"]
    )


def test_cyclic_volume_reference_raises_an_actionable_error(write_gdml):
    path = write_gdml(
        solids='<box name="shape" x="2" y="4" z="6" lunit="mm"/>',
        structure="""
<volume name="a"><materialref ref="G4_Si"/><solidref ref="shape"/>
  <physvol><volumeref ref="b"/></physvol>
</volume>
<volume name="b"><materialref ref="G4_Si"/><solidref ref="shape"/>
  <physvol><volumeref ref="a"/></physvol>
</volume>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol><volumeref ref="a"/></physvol>
</volume>""",
    )
    with pytest.raises(ValueError, match="Cyclic GDML volume reference: a"):
        scene_builder(path).build()


def test_cyclic_assembly_reference_raises_an_actionable_error(write_gdml):
    path = write_gdml(
        solids='<box name="shape" x="2" y="4" z="6" lunit="mm"/>',
        structure="""
<assembly name="group"><physvol><volumeref ref="group"/></physvol></assembly>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol><volumeref ref="group"/></physvol>
</volume>""",
    )
    with pytest.raises(ValueError, match="Cyclic GDML assembly reference: group"):
        scene_builder(path).build()


def test_cyclic_boolean_solid_reference_raises_an_actionable_error(write_gdml):
    path = write_gdml(
        solids="""
<box name="shape" x="2" y="4" z="6" lunit="mm"/>
<subtraction name="a"><first ref="b"/><second ref="shape"/></subtraction>
<subtraction name="b"><first ref="a"/><second ref="shape"/></subtraction>""",
        structure="""
<volume name="leaf"><materialref ref="G4_Si"/><solidref ref="a"/></volume>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol><volumeref ref="leaf"/></physvol>
</volume>""",
    )
    with pytest.raises(ValueError, match="Cyclic GDML solid reference: a"):
        scene_builder(path).build()


def test_excessive_volume_nesting_raises_an_actionable_error(write_gdml):
    depth = 300
    layers = [
        '<volume name="v0"><materialref ref="G4_Si"/><solidref ref="shape"/></volume>'
    ]
    for i in range(1, depth):
        layers.append(
            f'<volume name="v{i}"><materialref ref="Galactic"/>'
            f'<solidref ref="shape"/>'
            f'<physvol><volumeref ref="v{i - 1}"/></physvol></volume>'
        )
    layers.append(f"""
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol><volumeref ref="v{depth - 1}"/></physvol>
</volume>""")
    path = write_gdml(
        solids='<box name="shape" x="2" y="4" z="6" lunit="mm"/>',
        structure="".join(layers),
    )
    with pytest.raises(ValueError, match="nesting exceeds the supported depth"):
        scene_builder(path).build()


def test_missing_volume_reference_is_skipped_with_a_warning(write_gdml):
    path = write_gdml(
        solids="",
        structure="""<volume name="world"><materialref ref="Galactic"/>
<solidref ref="world_box"/>
<physvol name="bad"><volumeref ref="absent"/></physvol></volume>""",
    )
    scene = scene_builder(path).build()
    assert scene["meta"]["objects"] == 0
    assert any(
        "Missing volume reference: absent" in w for w in scene["meta"]["warnings"]
    )


def test_unsupported_solid_is_reported_as_skipped(single_volume):
    scene = scene_builder(
        single_volume('<sphere name="shape" rmin="0" rmax="2"/>')
    ).build()
    assert scene["positions"] == []
    assert any("Unsupported solid type sphere" in w for w in scene["meta"]["warnings"])


def test_replicavol_is_reported_and_the_mother_still_renders(write_gdml):
    # Replicas fill their mother, so it is still drawn as an approximation.
    path = write_gdml(
        solids='<box name="shape" x="2" y="4" z="6" lunit="mm"/>',
        structure="""
<volume name="leaf"><materialref ref="G4_Si"/><solidref ref="shape"/>
  <replicavol/>
</volume>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol><volumeref ref="leaf"/></physvol>
</volume>""",
    )
    scene = scene_builder(path).build()
    assert scene["meta"]["objects"] == 1
    assert any(
        "Unsupported GDML daughter construct 'replicavol' in volume 'leaf'" in w
        for w in scene["meta"]["warnings"]
    )


def test_paramvol_is_reported_and_treated_as_a_container(write_gdml):
    # Parameterised daughters are not expanded, so the mother is hidden by
    # default like any other container, rather than drawn as an approximation.
    path = write_gdml(
        solids='<box name="shape" x="2" y="4" z="6" lunit="mm"/>',
        structure="""
<volume name="leaf"><materialref ref="G4_Si"/><solidref ref="shape"/>
  <paramvol/>
</volume>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol><volumeref ref="leaf"/></physvol>
</volume>""",
    )
    hidden = scene_builder(path).build()
    shown = scene_builder(path, include_containers=True).build()
    assert hidden["meta"]["objects"] == 0
    assert shown["meta"]["objects"] == 1
    assert any(
        "Unsupported GDML daughter construct 'paramvol' in volume 'leaf'" in w
        for w in hidden["meta"]["warnings"]
    )


def test_constants_and_variables_chain_into_a_dependent_position(single_volume):
    path = single_volume(
        '<box name="shape" x="half_x*2" y="4" z="6" lunit="mm"/>',
        placement='<positionref ref="shift"/>',
        define="""
<constant name="half_x" value="1"/>
<variable name="box_x" value="half_x*2"/>
<position name="shift" x="box_x*5" y="0" z="0" unit="mm"/>""",
    )
    scene = scene_builder(path).build()
    assert scene["meta"]["warnings"] == []
    assert scene["meta"]["bbox"] == pytest.approx([9, -2, -3, 11, 2, 3])


def test_unitless_expression_uses_the_predefined_pi(single_volume):
    path = single_volume(
        '<tube name="shape" rmin="0" rmax="3" z="4" '
        'startphi="0" deltaphi="2*pi" lunit="mm"/>'
    )
    scene = scene_builder(path).build()
    assert scene["meta"]["warnings"] == []
    assert scene["meta"]["bbox"] == pytest.approx([-3, -3, -2, 3, 3, 2])


def test_unevaluable_expression_is_reported_and_falls_back_to_its_default(
    single_volume,
):
    path = single_volume('<box name="shape" x="unknown_name" y="4" z="6" lunit="mm"/>')
    scene = scene_builder(path).build()
    assert any(
        "Could not evaluate GDML expression x='unknown_name'" in w
        for w in scene["meta"]["warnings"]
    )
    assert scene["meta"]["bbox"] == [0, -2, -3, 0, 2, 3]


def test_named_position_reference_moves_the_placed_volume(single_volume):
    path = single_volume(
        placement='<positionref ref="shift"/>',
        define='<position name="shift" x="10" y="20" z="30" unit="mm"/>',
    )
    assert scene_builder(path).build()["meta"]["bbox"] == [9, 18, 27, 11, 22, 33]


def test_missing_position_reference_raises_an_actionable_error(single_volume):
    path = single_volume(placement='<positionref ref="absent"/>')
    with pytest.raises(ValueError, match="position reference: absent"):
        scene_builder(path).build()


def test_missing_scale_reference_raises_an_actionable_error(single_volume):
    path = single_volume(placement='<scaleref ref="absent"/>')
    with pytest.raises(ValueError, match="scale reference: absent"):
        scene_builder(path).build()


@pytest.mark.parametrize("mode", ["standalone", "lazy"])
def test_assembly_placements_are_relative_to_the_assembly_itself(write_gdml, mode):
    # Assemblies have no solid of their own; only their physvols are placed.
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
    expected = [9, -2, -3, 11, 2, 3]
    if mode == "standalone":
        scene = scene_builder(path).build()
        assert scene["meta"]["objects"] == 1
        assert scene["meta"]["bbox"] == expected
    else:
        builder = lazy_builder(path)
        builder.build_chunks()
        assert builder.total_objects == 1
        assert builder.global_bbox_min + builder.global_bbox_max == pytest.approx(
            expected
        )


def test_union_positions_its_second_operand_without_claiming_csg_fidelity(
    single_volume,
):
    path = single_volume("""
<box name="a" x="2" y="4" z="6" lunit="mm"/>
<box name="b" x="4" y="2" z="2" lunit="mm"/>
<union name="shape"><first ref="a"/><second ref="b"/>
  <position x="10" y="0" z="0" unit="mm"/>
</union>""")
    scene = scene_builder(path).build()
    # Correct bounds do not establish interior-face removal or true CSG fidelity.
    assert scene["meta"]["bbox"] == [-1, -2, -3, 12, 2, 3]


@pytest.mark.parametrize("operation", ["subtraction", "intersection"])
def test_boolean_fallback_is_identified_as_an_approximation(single_volume, operation):
    path = single_volume(f"""
<box name="a" x="2" y="4" z="6" lunit="mm"/>
<box name="b" x="1" y="1" z="1" lunit="mm"/>
<{operation} name="shape"><first ref="a"/><second ref="b"/></{operation}>""")
    scene = scene_builder(path).build()
    assert scene["meta"]["bbox"] == [-1, -2, -3, 1, 2, 3]
    assert any(
        operation in warning and "approximated" in warning
        for warning in scene["meta"]["warnings"]
    )


def test_union_first_operand_transform_is_applied_before_combining(single_volume):
    path = single_volume("""
<box name="a" x="2" y="4" z="6" lunit="mm"/>
<box name="b" x="4" y="2" z="2" lunit="mm"/>
<union name="shape"><first ref="a"/>
  <firstposition x="-5" y="0" z="0" unit="mm"/>
  <second ref="b"/>
</union>""")
    scene = scene_builder(path).build()
    assert scene["meta"]["bbox"] == pytest.approx([-6, -2, -3, 2, 2, 3])


def test_subtraction_first_operand_transform_is_applied_before_approximation(
    single_volume,
):
    path = single_volume("""
<box name="a" x="2" y="4" z="6" lunit="mm"/>
<box name="b" x="1" y="1" z="1" lunit="mm"/>
<subtraction name="shape"><first ref="a"/>
  <firstposition x="5" y="0" z="0" unit="mm"/>
  <second ref="b"/>
</subtraction>""")
    scene = scene_builder(path).build()
    assert scene["meta"]["bbox"] == pytest.approx([4, -2, -3, 6, 2, 3])
