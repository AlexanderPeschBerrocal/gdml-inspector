"""Original, small GDML fixtures; no external detector files or native tools."""

import importlib.util
import threading
from pathlib import Path

import pytest

from gdml_inspector import cli
from gdml_inspector.server import create_server

from .lazy_scene import GDML_TEXT, QUALITY

# The browser tests need the optional `browser` dependency group (see
# docs/testing.md); without it they are skipped at collection time rather
# than failing on a missing `page` fixture.
collect_ignore_glob = (
    [] if importlib.util.find_spec("playwright") else ["test_*browser*.py"]
)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    setattr(item, f"rep_{call.when}", outcome.get_result())


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


@pytest.fixture
def served():
    """``served(directory) -> base URL``: serves a directory over real
    loopback HTTP (lazy chunks cannot be fetched over file://). Every
    server the test started is shut down and closed on teardown."""
    running = []

    def serve(directory):
        server = create_server(Path(directory), 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        running.append((server, thread))
        return f"http://127.0.0.1:{server.server_port}"

    yield serve
    for server, thread in running:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class PageWatcher:
    """Records every way a page can visibly fail: console errors, uncaught
    exceptions, failed requests and HTTP error responses."""

    def __init__(self, page):
        self.page = page
        self.problems = []
        page.on("console", self._console)
        page.on("pageerror", lambda exc: self._add(f"uncaught exception: {exc}"))
        # The lazy viewer deliberately aborts a request for a layer that is
        # switched off before its chunk arrives; that is cancellation, not a
        # failure (test_lazy_loading_browser.py asserts the aborts explicitly).
        page.on(
            "requestfailed",
            lambda request: (
                None
                if "ERR_ABORTED" in str(request.failure)
                else self._add(f"request failed: {request.url} ({request.failure})")
            ),
        )
        page.on("response", self._response)

    def _add(self, problem):
        self.problems.append(problem)

    def _console(self, message):
        if message.type == "error":
            self._add(f"console error: {message.text}")

    def _response(self, response):
        if response.status >= 400 and not response.url.endswith("/favicon.ico"):
            self._add(f"HTTP {response.status}: {response.url}")

    def assert_clean(self):
        assert not self.problems, "browser problems:\n" + "\n".join(self.problems)


@pytest.fixture
def watched_page(page, request, output_path):
    """``page`` plus error collection; on failure the collected problems
    are written to ``browser-log.txt`` beside Playwright's own artifacts so
    CI uploads them with the screenshot and trace."""
    watcher = PageWatcher(page)
    yield watcher
    report = getattr(request.node, "rep_call", None)
    if report is not None and report.failed:
        directory = Path(output_path)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "browser-log.txt").write_text(
            "\n".join(watcher.problems) or "no browser problems recorded",
            encoding="utf-8",
        )


@pytest.fixture(scope="session")
def webgl_renderer(browser):
    """Fails visibly, naming the reason, if headless Chromium cannot create
    a WebGL context -- every viewer test would otherwise fail obscurely.
    Returns the renderer string (software rendering shows up here)."""
    context = browser.new_context()
    page = context.new_page()
    try:
        renderer = page.evaluate(
            """() => {
              const gl = document.createElement('canvas').getContext('webgl');
              return gl ? gl.getParameter(gl.RENDERER) : null;
            }"""
        )
    finally:
        context.close()
    assert renderer, (
        "WebGL is unavailable in this headless Chromium; the browser tests "
        "need at least software rendering (see docs/testing.md)."
    )
    print(f"WebGL renderer: {renderer}")
    return renderer


@pytest.fixture(scope="module")
def lazy_scene_gdml(tmp_path_factory):
    directory = tmp_path_factory.mktemp("lazy-pick-src")
    path = directory / "scene.gdml"
    path.write_text(
        GDML_TEXT,
        encoding="utf-8",
    )
    return path


@pytest.fixture(scope="module")
def lazy_scene_dir(tmp_path_factory, lazy_scene_gdml):
    directory = tmp_path_factory.mktemp("lazy-pick")
    assert (
        cli.main(
            [
                "--lazy",
                str(lazy_scene_gdml),
                *QUALITY,
                "--out",
                str(directory / "v.html"),
            ]
        )
        == 0
    )
    return directory


@pytest.fixture(scope="module")
def lazy_scene_baked_dir(tmp_path_factory, lazy_scene_gdml):
    directory = tmp_path_factory.mktemp("lazy-pick-baked")
    assert (
        cli.main(
            [
                "--lazy",
                "--instancing",
                "off",
                str(lazy_scene_gdml),
                *QUALITY,
                "--out",
                str(directory / "v.html"),
            ]
        )
        == 0
    )
    return directory


@pytest.fixture(scope="module")
def lazy_scene_instanced_dir(tmp_path_factory, lazy_scene_gdml):
    directory = tmp_path_factory.mktemp("lazy-pick-instanced")
    assert (
        cli.main(
            [
                "--lazy",
                "--instancing",
                "always",
                str(lazy_scene_gdml),
                *QUALITY,
                "--out",
                str(directory / "v.html"),
            ]
        )
        == 0
    )
    return directory


@pytest.fixture(scope="module")
def lazy_scene_standalone(tmp_path_factory, lazy_scene_gdml):
    out = tmp_path_factory.mktemp("lazy-pick-standalone") / "s.html"
    assert cli.main([str(lazy_scene_gdml), *QUALITY, "--out", str(out)]) == 0
    return out
