"""--view-state: loading, refusing what can never apply, embedding, and the
token-substitution hardening that came with it (issue #10)."""

import json
import shutil
from pathlib import Path

import pytest

from gdml_inspector import cli, lazy
from gdml_inspector.viewstate import load_view_state, stale_warning

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "detector.gdml"
BUILD = ["--backend", "python", "--quality", "fast"]


def valid_state(**extra):
    return {
        "format": "gdml-inspector-view",
        "version": 1,
        "viewer": "standalone",
        "camera": {
            "yaw": 0.5,
            "pitch": 0.2,
            "dist": 900,
            "pan": [0, 0],
            "up": [0, 1, 0],
        },
        **extra,
    }


def write(path, state):
    path.write_text(json.dumps(state), encoding="utf-8")
    return path


def test_a_valid_state_loads(tmp_path):
    state = valid_state()
    assert load_view_state(write(tmp_path / "v.json", state)) == state


@pytest.mark.parametrize(
    "content, message",
    [
        ("{not json", "not valid JSON"),
        ('["a list"]', "not a gdml-inspector view-state file"),
        (
            '{"format": "something-else", "version": 1}',
            "not a gdml-inspector view-state file",
        ),
        (
            '{"format": "gdml-inspector-view", "version": "1"}',
            "invalid view-state version",
        ),
        (
            '{"format": "gdml-inspector-view", "version": 0}',
            "invalid view-state version",
        ),
        (
            '{"format": "gdml-inspector-view", "version": 99, "viewer": "lazy"}',
            "newer gdml-inspector",
        ),
        (
            '{"format": "gdml-inspector-view", "version": 1, "viewer": "web"}',
            '"viewer" must be',
        ),
    ],
)
def test_unusable_files_are_refused_with_guidance(tmp_path, content, message):
    path = tmp_path / "v.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_view_state(path)


def test_a_missing_file_is_refused(tmp_path):
    with pytest.raises(ValueError, match="cannot read"):
        load_view_state(tmp_path / "absent.json")


def test_stale_warning_only_for_different_geometry():
    state = valid_state(savedFor={"inputSha256": "a" * 64})
    assert stale_warning(state, "a" * 64) is None
    assert "different geometry" in stale_warning(state, "b" * 64)
    assert stale_warning(valid_state(), "b" * 64) is None


@pytest.mark.parametrize("mode", [[], ["--lazy"]])
def test_a_bad_view_state_fails_the_export_with_exit_2(tmp_path, capsys, mode):
    bad = tmp_path / "bad.json"
    bad.write_text("{nope", encoding="utf-8")
    out = tmp_path / "viewer.html"
    code = cli.main(
        [*mode, str(EXAMPLE), *BUILD, "--view-state", str(bad), "--out", str(out)]
    )
    assert code == 2
    assert "--view-state" in capsys.readouterr().err
    assert not out.exists()


def test_standalone_embeds_the_view_state(tmp_path):
    state = valid_state()
    out = tmp_path / "viewer.html"
    args = [
        str(EXAMPLE),
        *BUILD,
        "--view-state",
        str(write(tmp_path / "v.json", state)),
        "--out",
        str(out),
    ]
    assert cli.main(args) == 0
    document = out.read_text(encoding="utf-8")
    embedded = document.split("const INITIAL_VIEW = ", 1)[1].split(";\n", 1)[0]
    assert json.loads(embedded) == state


def test_standalone_without_a_view_state_embeds_null(tmp_path):
    out = tmp_path / "viewer.html"
    assert cli.main([str(EXAMPLE), *BUILD, "--out", str(out)]) == 0
    assert "const INITIAL_VIEW = null;" in out.read_text(encoding="utf-8")


def test_lazy_embeds_the_view_state_and_reuse_keeps_or_replaces_it(tmp_path):
    source = tmp_path / "detector.gdml"
    shutil.copy(EXAMPLE, source)
    out = tmp_path / "viewer.html"
    first = valid_state(viewer="lazy")
    args = ["--lazy", str(source), *BUILD, "--out", str(out)]
    assert (
        cli.main([*args, "--view-state", str(write(tmp_path / "a.json", first))]) == 0
    )
    assert lazy.read_existing_manifest(out)["viewState"] == first
    assert cli.main([*args, "--reuse-assets", "--default-load", "all"]) == 0
    assert lazy.read_existing_manifest(out)["viewState"] == first
    second = valid_state(viewer="lazy", display={"opacity": 30})
    assert (
        cli.main(
            [
                *args,
                "--reuse-assets",
                "--view-state",
                str(write(tmp_path / "b.json", second)),
            ]
        )
        == 0
    )
    assert lazy.read_existing_manifest(out)["viewState"] == second


def test_exporting_with_a_view_saved_for_other_geometry_warns(tmp_path, capsys):
    state = valid_state(savedFor={"inputSha256": "0" * 64})
    out = tmp_path / "viewer.html"
    args = [
        str(EXAMPLE),
        *BUILD,
        "--view-state",
        str(write(tmp_path / "v.json", state)),
        "--out",
        str(out),
    ]
    assert cli.main(args) == 0
    assert "different geometry" in capsys.readouterr().err


@pytest.mark.parametrize("lazy_mode", [False, True])
def test_gdml_names_that_look_like_template_tokens_are_inserted_verbatim(
    tmp_path, write_gdml, lazy_mode
):
    # Names come from the GDML and are embedded as JSON; a name equal to a
    # template token must never be mistaken for one and replaced.
    path = write_gdml(
        solids='<box name="shape" x="2" y="4" z="6" lunit="mm"/>',
        structure="""
<volume name="leaf"><materialref ref="__VIEW_STATE__"/><solidref ref="shape"/></volume>
<volume name="world"><materialref ref="Galactic"/><solidref ref="world_box"/>
  <physvol name="__SCENE__"><volumeref ref="leaf"/></physvol>
</volume>""",
    )
    out = tmp_path / "viewer.html"
    assert (
        cli.main(
            [*(["--lazy"] if lazy_mode else []), str(path), *BUILD, "--out", str(out)]
        )
        == 0
    )
    document = out.read_text(encoding="utf-8")
    if lazy_mode:
        assert "__VIEW_STATE__" in lazy.read_existing_manifest(out)["materials"]
    else:
        scene = json.loads(
            document.split("const SCENE = ", 1)[1].split(";\nconst HIERARCHY =", 1)[0]
        )
        assert "__VIEW_STATE__" in scene["meta"]["materials"]
