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
    (repeated-placement branches), a container volume with its own
    rendered child (expand/collapse, container labelling), and a front/back
    pair of identically-sized, axis-aligned boxes directly behind one
    another along +Z (for depth-correct-picking/occlusion tests)."""
    path = write_gdml(
        solids="""
<box name="shape" x="2" y="4" z="6" lunit="mm"/>
<box name="overlap_shape" x="20" y="20" z="6" lunit="mm"/>""",
        structure="""
<volume name="pixel"><materialref ref="G4_Si"/><solidref ref="shape"/></volume>
<volume name="child"><materialref ref="G4_Si"/><solidref ref="shape"/></volume>
<volume name="container"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol name="inner"><volumeref ref="child"/>
    <position x="5" y="0" z="0" unit="mm"/>
  </physvol>
</volume>
<volume name="empty_container"><materialref ref="Galactic"/><solidref ref="world_box"/></volume>
<volume name="front_box"><materialref ref="G4_Si"/><solidref ref="overlap_shape"/></volume>
<volume name="back_box"><materialref ref="G4_Si"/><solidref ref="overlap_shape"/></volume>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol name="pixel_a"><volumeref ref="pixel"/>
    <position x="-50" y="0" z="0" unit="mm"/>
  </physvol>
  <physvol name="pixel_b"><volumeref ref="pixel"/>
    <position x="50" y="0" z="0" unit="mm"/>
  </physvol>
  <physvol name="group"><volumeref ref="container"/></physvol>
  <physvol name="empty"><volumeref ref="empty_container"/></physvol>
  <physvol name="front"><volumeref ref="front_box"/>
    <position x="0" y="0" z="100" unit="mm"/>
  </physvol>
  <physvol name="back"><volumeref ref="back_box"/>
    <position x="0" y="0" z="-100" unit="mm"/>
  </physvol>
</volume>""",
    )
    builder = HierarchyGDMLSceneBuilder(path, **DEFAULT_OPTIONS)
    scene = builder.build()
    nodes = builder.hierarchy_nodes(scene)
    solids = builder.solids_table()
    output = tmp_path / "viewer.html"
    write_html(scene, nodes, solids, output)
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
        "world/front",
        "world/back",
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


_PROJECT_JS = """([wx, wy, wz]) => {
  const cx = viewCenter[0] + panX, cy = viewCenter[1] + panY, cz = viewCenter[2];
  const eye = [cx + dist*Math.cos(pitch)*Math.sin(yaw), cy + dist*Math.sin(pitch), cz + dist*Math.cos(pitch)*Math.cos(yaw)];
  const mvp = mat4mul(perspective(45*Math.PI/180, canvas.width/canvas.height, radius/500, radius*20), lookAt(eye, [cx,cy,cz], cameraUp));
  const tx=mvp[0]*wx+mvp[4]*wy+mvp[8]*wz+mvp[12], ty=mvp[1]*wx+mvp[5]*wy+mvp[9]*wz+mvp[13], tw=mvp[3]*wx+mvp[7]*wy+mvp[11]*wz+mvp[15];
  const rect = canvas.getBoundingClientRect();
  return [(tx/tw*0.5+0.5)*rect.width + rect.left, (-ty/tw*0.5+0.5)*rect.height + rect.top];
}"""


def click_world_point(page, world_point):
    """Project a world-space point to page coordinates using the viewer's
    own camera/projection functions (not a reimplementation of them), and
    fire a real mouse click there -- exercising the actual DOM event path,
    not a shortcut that calls pickAt()/selectObject() directly."""
    sx, sy = page.evaluate(_PROJECT_JS, world_point)
    page.mouse.click(sx, sy)


def click_object_center(page, path):
    bbox = page.evaluate(f"SCENE.objects.find(o => o.path === '{path}').bbox")
    center = [(bbox[0] + bbox[3]) / 2, (bbox[1] + bbox[4]) / 2, (bbox[2] + bbox[5]) / 2]
    click_world_point(page, center)


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


def test_clicking_a_box_selects_it_and_populates_the_inspector(opened):
    page, errors = opened
    page.click("#viewXYPos")
    page.wait_for_timeout(50)
    click_object_center(page, "world/pixel_a")
    page.wait_for_timeout(100)
    assert page.evaluate("selectedPath") == "world/pixel_a"
    pixel_a_id = page.evaluate("SCENE.objects.find(o => o.path === 'world/pixel_a').id")
    assert page.evaluate("selectedObjectId") == pixel_a_id
    assert page.eval_on_selector("#detailsContent", "el => el.style.display") == ""
    assert page.inner_text("#detailPath") == "world/pixel_a"
    assert page.inner_text("#detailSolidType") == "box"
    assert "x:" in page.inner_text("#detailParams")
    # The highlight buffer should mark exactly this object's vertices.
    highlighted_ids = page.evaluate(
        "Array.from(new Set(Array.from(ids).filter((id, i) => highlight[i] > 0)))"
    )
    assert highlighted_ids == [pixel_a_id]
    assert errors == []


def test_picking_is_depth_correct_through_occluding_geometry(opened):
    # "front" (z=+100) and "back" (z=-100) share the same x/y footprint;
    # viewed down -Z, a click at their shared screen position must resolve
    # to the nearer one regardless of draw order -- the exact case
    # vertex-sampling picking could get wrong.
    page, errors = opened
    page.click("#viewXYPos")
    page.wait_for_timeout(50)
    click_world_point(page, [0, 0, 100])
    page.wait_for_timeout(100)
    assert page.evaluate("selectedPath") == "world/front"
    assert errors == []


def test_hidden_object_is_not_pickable_and_does_not_block_what_is_behind_it(opened):
    page, errors = opened
    page.click("#viewXYPos")
    page.wait_for_timeout(50)
    page.query_selector('[data-path="world/front"] input[type=checkbox]').click()
    page.wait_for_timeout(50)
    click_world_point(page, [0, 0, 100])
    page.wait_for_timeout(100)
    # The front box is hidden, so the click passes through to the back one.
    assert page.evaluate("selectedPath") == "world/back"
    assert errors == []


def test_clicking_empty_space_clears_the_selection(opened):
    page, _errors = opened
    page.click("#viewXYPos")
    page.wait_for_timeout(50)
    click_object_center(page, "world/pixel_a")
    page.wait_for_timeout(100)
    assert page.evaluate("selectedObjectId") is not None
    rect = page.evaluate(
        "(() => { const r = canvas.getBoundingClientRect(); return [r.left, r.top]; })()"
    )
    page.mouse.click(
        rect[0] + 5, rect[1] + 5
    )  # a canvas corner, away from all geometry
    page.wait_for_timeout(100)
    assert page.evaluate("selectedObjectId") is None
    assert page.eval_on_selector("#detailsContent", "el => el.style.display") == "none"


def test_selecting_a_container_tree_node_shows_a_reduced_inspector(opened):
    page, _errors = opened
    row = page.query_selector('[data-path="world/group"]')
    row.click()
    page.wait_for_timeout(50)
    assert page.evaluate("selectedObjectId") is None
    assert "container" in page.inner_text("#detailMaterial")
    assert page.inner_text("#detailSolidType") == "--"


def test_deselecting_when_the_selected_object_is_filtered_out(opened):
    page, _errors = opened
    page.click("#viewXYPos")
    page.wait_for_timeout(50)
    click_object_center(page, "world/pixel_a")
    page.wait_for_timeout(100)
    assert page.evaluate("selectedObjectId") is not None
    material = page.evaluate(
        "SCENE.objects.find(o => o.path === 'world/pixel_a').material"
    )
    page.eval_on_selector(f'input[data-material="{material}"]', "el => el.click()")
    page.wait_for_timeout(100)
    assert page.evaluate("selectedObjectId") is None
    assert page.eval_on_selector("#detailsContent", "el => el.style.display") == "none"


def test_copy_buttons_do_not_throw(opened):
    page, errors = opened
    page.click("#viewXYPos")
    page.wait_for_timeout(50)
    click_object_center(page, "world/pixel_a")
    page.wait_for_timeout(100)
    page.click("#detailCopyPath")
    page.click("#detailCopyName")
    page.wait_for_timeout(50)
    assert errors == []


def test_picking_is_correct_after_resize(opened):
    page, _errors = opened
    page.click("#viewXYPos")
    page.wait_for_timeout(50)
    page.set_viewport_size({"width": 700, "height": 500})
    page.wait_for_timeout(100)
    click_world_point(page, [0, 0, 100])
    page.wait_for_timeout(100)
    assert page.evaluate("selectedPath") == "world/front"


def test_picking_is_correct_at_high_device_pixel_ratio(viewer_path, browser):
    context = browser.new_context(
        viewport={"width": 900, "height": 700}, device_scale_factor=2
    )
    page = context.new_page()
    errors = []
    page.on(
        "console", lambda msg: errors.append(msg.text) if msg.type == "error" else None
    )
    page.goto(viewer_path.resolve().as_uri())
    page.wait_for_selector("#hierarchyTree .tree-row")
    page.click("#viewXYPos")
    page.wait_for_timeout(50)
    click_world_point(page, [0, 0, 100])
    page.wait_for_timeout(100)
    assert page.evaluate("window.devicePixelRatio") == 2
    assert page.evaluate("selectedPath") == "world/front"
    assert errors == []
    context.close()
