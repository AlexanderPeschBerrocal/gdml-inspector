"""Real headless-browser verification of the standalone viewer's hierarchy
panel: no Python unit test can exercise embedded JS, so this drives an
actual generated HTML file in Chromium via Playwright and asserts on the
resulting DOM/JS state (not pixel comparisons)."""

import pytest

from gdml_inspector.standalone import HierarchyGDMLSceneBuilder, write_html

from .helpers import DEFAULT_OPTIONS


@pytest.fixture
def viewer_path(tmp_path, write_gdml):
    """A world with: two sibling placements of the same logical volume
    (repeated-placement branches), and a container volume with its own
    rendered child (expand/collapse, container labelling)."""
    path = write_gdml(
        solids='<box name="shape" x="2" y="4" z="6" lunit="mm"/>',
        structure="""
<volume name="pixel"><materialref ref="G4_Si"/><solidref ref="shape"/></volume>
<volume name="child"><materialref ref="G4_Si"/><solidref ref="shape"/></volume>
<volume name="container"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol name="inner"><volumeref ref="child"/>
    <position x="5" y="0" z="0" unit="mm"/>
  </physvol>
</volume>
<volume name="empty_container"><materialref ref="Galactic"/><solidref ref="world_box"/></volume>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol name="pixel_a"><volumeref ref="pixel"/>
    <position x="-50" y="0" z="0" unit="mm"/>
  </physvol>
  <physvol name="pixel_b"><volumeref ref="pixel"/>
    <position x="50" y="0" z="0" unit="mm"/>
  </physvol>
  <physvol name="group"><volumeref ref="container"/></physvol>
  <physvol name="empty"><volumeref ref="empty_container"/></physvol>
</volume>""",
    )
    builder = HierarchyGDMLSceneBuilder(path, **DEFAULT_OPTIONS)
    scene = builder.build()
    nodes = builder.hierarchy_nodes(scene)
    output = tmp_path / "viewer.html"
    write_html(scene, nodes, output)
    return output


@pytest.fixture
def opened(page, viewer_path):
    errors = []
    page.on(
        "console",
        lambda msg: errors.append(msg.text) if msg.type == "error" else None,
    )
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(viewer_path.resolve().as_uri())
    page.wait_for_selector("#hierarchyTree .tree-row")
    return page, errors


def tree_rows(page):
    return page.query_selector_all("#hierarchyTree .tree-row")


def row_path(row):
    return row.get_attribute("data-path")


def test_tree_renders_with_no_console_errors(opened):
    page, errors = opened
    assert errors == []
    assert len(tree_rows(page)) >= 1


def test_world_starts_expanded_showing_its_direct_children(opened):
    page, _errors = opened
    paths = {row_path(row) for row in tree_rows(page)}
    assert paths == {
        "world",
        "world/pixel_a",
        "world/pixel_b",
        "world/group",
        "world/empty",
    }


def test_repeated_logical_volume_renders_two_independent_rows(opened):
    page, _errors = opened
    rows = {row_path(row): row for row in tree_rows(page)}
    label_a = rows["world/pixel_a"].query_selector(".tree-label").inner_text()
    label_b = rows["world/pixel_b"].query_selector(".tree-label").inner_text()
    assert "pixel_a" in label_a
    assert "pixel_b" in label_b
    checkbox_a = rows["world/pixel_a"].query_selector("input[type=checkbox]")
    checkbox_b = rows["world/pixel_b"].query_selector("input[type=checkbox]")
    checkbox_a.click()
    page.wait_for_timeout(50)
    assert checkbox_a.is_checked() is False
    assert checkbox_b.is_checked() is True


def test_expand_collapse_materialises_and_removes_child_rows(opened):
    page, _errors = opened
    rows = {row_path(row): row for row in tree_rows(page)}
    toggle = rows["world/group"].query_selector(".tree-toggle")
    assert row_path_present(page, "world/group/inner") is False
    toggle.click()
    page.wait_for_timeout(50)
    assert row_path_present(page, "world/group/inner") is True
    toggle = page.query_selector('[data-path="world/group"] .tree-toggle')
    toggle.click()
    page.wait_for_timeout(50)
    assert row_path_present(page, "world/group/inner") is False


def row_path_present(page, path):
    return page.query_selector(f'[data-path="{path}"]') is not None


def test_hide_checkbox_updates_object_visibility(opened):
    page, _errors = opened
    checkbox = page.query_selector('[data-path="world/pixel_a"] input[type=checkbox]')
    checkbox.click()
    page.wait_for_timeout(50)
    pixel_a_id = page.evaluate("SCENE.objects.find(o => o.path === 'world/pixel_a').id")
    assert page.evaluate(f"objectVisible[{pixel_a_id}]") == 0
    pixel_b_id = page.evaluate("SCENE.objects.find(o => o.path === 'world/pixel_b').id")
    assert page.evaluate(f"objectVisible[{pixel_b_id}]") == 1


def test_isolate_restricts_visibility_to_the_selected_subtree(opened):
    page, _errors = opened
    isolate_btn = page.query_selector('[data-path="world/pixel_a"] .tree-action-btn')
    isolate_btn.click()
    page.wait_for_timeout(50)
    pixel_a_id = page.evaluate("SCENE.objects.find(o => o.path === 'world/pixel_a').id")
    pixel_b_id = page.evaluate("SCENE.objects.find(o => o.path === 'world/pixel_b').id")
    assert page.evaluate(f"objectVisible[{pixel_a_id}]") == 1
    assert page.evaluate(f"objectVisible[{pixel_b_id}]") == 0
    assert page.is_visible("#hierarchyClearIsolation")
    page.click("#hierarchyClearIsolation")
    page.wait_for_timeout(50)
    assert page.evaluate(f"objectVisible[{pixel_b_id}]") == 1


def test_focus_action_recenters_and_zooms_the_camera(opened):
    page, _errors = opened
    dist_before = page.evaluate("dist")
    buttons = page.query_selector_all('[data-path="world/pixel_a"] .tree-action-btn')
    focus_btn = buttons[1]
    focus_btn.click()
    page.wait_for_timeout(50)
    dist_after = page.evaluate("dist")
    assert dist_after < dist_before


def test_focus_on_empty_container_falls_back_to_an_ancestor_bbox(opened):
    # "world/empty" has no mesh and no rendered descendants of its own at
    # all; focusing it must walk up to the nearest ancestor that has any
    # rather than leaving the camera on a meaningless empty bbox.
    page, errors = opened
    radius = page.evaluate("radius")
    focus_btn = page.query_selector_all('[data-path="world/empty"] .tree-action-btn')[1]
    focus_btn.click()
    page.wait_for_timeout(50)
    assert errors == []
    assert page.evaluate("dist") > 0
    assert page.evaluate("radius") == radius  # the global bbox is untouched


def test_search_expands_ancestors_and_marks_matches(opened):
    page, _errors = opened
    page.fill("#hierarchySearch", "child")
    page.wait_for_timeout(50)
    assert row_path_present(page, "world/group/inner") is True
    match = page.query_selector('[data-path="world/group/inner"]')
    assert "tree-match" in match.get_attribute("class")
    other = page.query_selector('[data-path="world/pixel_a"]')
    assert "tree-dim" in other.get_attribute("class")
    page.fill("#hierarchySearch", "")
    page.wait_for_timeout(50)


def test_keyboard_navigation_moves_focus_and_toggles_visibility(opened):
    page, _errors = opened
    root = page.query_selector('[data-path="world"]')
    root.click()
    page.keyboard.press("ArrowDown")
    page.wait_for_timeout(50)
    focused_path = page.evaluate("document.activeElement.dataset.path")
    assert focused_path == "world/pixel_a"
    page.keyboard.press("Enter")
    page.wait_for_timeout(50)
    pixel_a_id = page.evaluate("SCENE.objects.find(o => o.path === 'world/pixel_a').id")
    assert page.evaluate(f"objectVisible[{pixel_a_id}]") == 0


def test_picking_an_object_reveals_it_in_the_hierarchy(opened):
    page, _errors = opened
    page.query_selector('[data-path="world/group"] .tree-toggle').click()
    page.wait_for_timeout(50)
    page.evaluate("revealInHierarchy('world/group/inner')")
    page.wait_for_timeout(50)
    row = page.query_selector('[data-path="world/group/inner"]')
    assert "tree-selected" in row.get_attribute("class")
