"""Robust lazy loading: bounded concurrent fetches, cancellation of stale
requests, local errors with retry, rapid toggling without leaks, eviction.

Chunk responses are held or altered with Playwright routes so the timing of
every request is under the test's control."""

import json

import pytest

from .browser_helpers import VIEWPORT, next_frames
from .lazy_scene import LAYER

KEYS = sorted(set(LAYER.values()))  # five distinct layers, one chunk each

_COUNT_BUFFERS = """() => {
  window.__live = new Set();
  const create = gl.createBuffer.bind(gl), remove = gl.deleteBuffer.bind(gl);
  gl.createBuffer = () => { const b = create(); window.__live.add(b); return b; };
  gl.deleteBuffer = b => { window.__live.delete(b); remove(b); };
}"""


class Held:
    """Holds every chunk request until the test releases it."""

    def __init__(self, page):
        self.pending = []
        self.requested = []
        page.route("**/*.bin", self._handle)

    def _handle(self, route):
        self.requested.append(route.request.url)
        self.pending.append(route)

    def release(self, count=None):
        routes, self.pending = (
            (self.pending, [])
            if count is None
            else (self.pending[:count], self.pending[count:])
        )
        for route in routes:
            route.continue_()
        return len(routes)


def open_lazy(watched_page, served, directory):
    page = watched_page.page
    page.set_viewport_size(VIEWPORT)
    page.goto(f"{served(directory)}/v.html")
    page.wait_for_selector("input[data-layer-key]")
    return page


def start_all(page):
    """Ask for every layer at once, without awaiting any of them (evaluate
    would wait for a returned promise, and held requests never finish)."""
    page.evaluate(
        f"void (window.__started = Promise.all({json.dumps(KEYS)}.map(k => setLayer(k, true))))"
    )


def wait_idle(page):
    page.wait_for_function(
        "Object.values(states).every(s => !s.loading) && activeFetches === 0"
    )


def live_buffers(page):
    return page.evaluate("window.__live.size")


def test_downloads_are_bounded_and_the_rest_queue(watched_page, served, lazy_scene_dir):
    page = open_lazy(watched_page, served, lazy_scene_dir)
    held = Held(page)
    page.evaluate("maxFetches = 2")
    start_all(page)
    page.wait_for_function("activeFetches === 2")
    assert len(held.requested) == 2
    assert "2 downloading, 3 queued" in page.inner_text("#loadProgress")
    # Finishing one lets exactly one more start.
    held.release(1)
    page.wait_for_function("Object.values(states).filter(s => s.loaded).length === 1")
    page.wait_for_function("activeFetches === 2")
    assert len(held.requested) == 3
    while held.pending or page.evaluate("fetchQueue.length"):
        held.release()
        next_frames(page)
    page.wait_for_function("Object.values(states).every(s => s.loaded)")
    assert page.evaluate("window.__started.then(() => true)")
    assert page.is_hidden("#loadProgress")
    assert watched_page.problems == []


def test_the_parallel_downloads_control_changes_the_bound(
    watched_page, served, lazy_scene_dir
):
    page = open_lazy(watched_page, served, lazy_scene_dir)
    held = Held(page)
    page.fill("#maxFetches", "1")
    page.dispatch_event("#maxFetches", "change")
    start_all(page)
    page.wait_for_function("activeFetches === 1")
    assert len(held.requested) == 1
    page.fill("#maxFetches", "3")
    page.dispatch_event("#maxFetches", "change")
    page.wait_for_function("activeFetches === 3")
    held.release()


def test_switching_a_queued_layer_off_never_requests_it(
    watched_page, served, lazy_scene_dir
):
    page = open_lazy(watched_page, served, lazy_scene_dir)
    held = Held(page)
    page.evaluate("maxFetches = 1")
    start_all(page)
    page.wait_for_function("activeFetches === 1")
    queued = page.evaluate(
        "MANIFEST.chunks.filter(c => states[c.key].loading && !states[c.key].controller).map(c => c.baseKey)"
    )
    assert len(queued) == len(KEYS) - 1
    victim = queued[-1]
    page.evaluate(f"setLayer({json.dumps(victim)}, false)")
    while held.pending or page.evaluate("fetchQueue.length"):
        held.release()
        next_frames(page)
    wait_idle(page)
    assert len(held.requested) == len(KEYS) - 1
    assert not any(
        victim.split("\x1f")[1].replace(" ", "_") in url for url in held.requested
    )
    assert page.evaluate(
        f"MANIFEST.chunks.filter(c => c.baseKey === {json.dumps(victim)}).every(c => !states[c.key].loaded)"
    )


def test_switching_an_in_flight_layer_off_aborts_it_and_keeps_nothing(
    watched_page, served, lazy_scene_dir
):
    page = open_lazy(watched_page, served, lazy_scene_dir)
    aborted = []
    page.on(
        "requestfailed",
        lambda r: aborted.append(r.url) if "ERR_ABORTED" in str(r.failure) else None,
    )
    held = Held(page)
    page.evaluate(_COUNT_BUFFERS)
    key = KEYS[0]
    page.evaluate(f"void setLayer({json.dumps(key)}, true)")
    page.wait_for_function("activeFetches === 1")
    page.evaluate(f"setLayer({json.dumps(key)}, false)")
    held.release()  # the late response must be discarded
    next_frames(page, 4)
    wait_idle(page)
    assert page.evaluate("Object.values(states).every(s => !s.loaded && !s.buffers)")
    assert live_buffers(page) == 0
    assert len(aborted) == 1


def test_off_on_off_ends_unloaded_and_on_off_on_ends_loaded_and_visible_once(
    watched_page, served, lazy_scene_dir
):
    page = open_lazy(watched_page, served, lazy_scene_dir)
    held = Held(page)
    key = json.dumps(KEYS[1])
    chunk = f"states[MANIFEST.chunks.find(c => c.baseKey === {key}).key]"
    page.evaluate(
        f"(async () => {{ setLayer({key}, true); setLayer({key}, false); setLayer({key}, true); }})()"
    )
    page.wait_for_function(f"{chunk}.loading")
    held.release()
    page.wait_for_function(f"{chunk}.loaded")
    wait_idle(page)
    assert page.evaluate(f"[{chunk}.visible, selectedLayers.has({key})]") == [
        True,
        True,
    ]
    page.evaluate(f"setLayer({key}, false)")
    assert page.evaluate(f"[{chunk}.loaded, {chunk}.visible]") == [False, False]


def test_rapid_toggling_never_leaks_buffers_or_leaves_hidden_geometry_loaded(
    watched_page, served, lazy_scene_dir
):
    page = open_lazy(watched_page, served, lazy_scene_dir)
    page.evaluate(_COUNT_BUFFERS)
    baseline = live_buffers(page)
    page.evaluate(
        """async (keys) => {
          let seed = 12345;
          const next = () => (seed = (seed * 1103515245 + 12345) & 0x7fffffff);
          const pending = [];
          for (let i = 0; i < 120; i++) {
            const key = keys[next() % keys.length];
            pending.push(setLayer(key, next() % 2 === 0));
            if (next() % 3 === 0) await new Promise(r => setTimeout(r, next() % 8));
          }
          await Promise.all(pending);
        }""",
        KEYS,
    )
    wait_idle(page)
    # Whatever the final selection is, loaded == selected and nothing hidden stays.
    snapshot = page.evaluate(
        """() => MANIFEST.chunks.map(c => [selectedLayers.has(c.baseKey), states[c.key].loaded, states[c.key].visible])"""
    )
    for selected, loaded, visible in snapshot:
        assert loaded == selected == visible
    loaded_chunks = sum(1 for _s, loaded, _v in snapshot if loaded)
    # Four buffers per loaded chunk: position, colour, id and visibility.
    assert live_buffers(page) == baseline + 4 * loaded_chunks
    for key in KEYS:
        page.evaluate(f"setLayer({json.dumps(key)}, false)")
    wait_idle(page)
    assert live_buffers(page) == baseline


def test_a_missing_chunk_fails_locally_and_can_be_retried(
    watched_page, served, lazy_scene_dir
):
    page = open_lazy(watched_page, served, lazy_scene_dir)
    key = KEYS[2]
    chunk_name = page.evaluate(
        f"MANIFEST.chunks.find(c => c.baseKey === {json.dumps(key)}).url"
    )
    attempts = []
    page.evaluate(
        """() => {
          window.__cacheModes = [];
          const original = window.fetch.bind(window);
          window.fetch = (url, options) => {
            window.__cacheModes.push([String(url), options && options.cache]);
            return original(url, options);
          };
        }"""
    )

    def handler(route):
        attempts.append(route.request.url)
        if len(attempts) == 1:
            route.fulfill(status=404, body="gone")
        else:
            route.continue_()

    page.route(f"**/{chunk_name.rsplit('/', 1)[-1]}", handler)
    page.evaluate(f"setLayer({json.dumps(key)}, true)")
    page.wait_for_selector(".layer-status.error")
    status = page.inner_text(".layer-status.error")
    assert "failed (1)" in status
    assert chunk_name.rsplit("/", 1)[-1] in page.get_attribute(
        ".layer-status.error span", "title"
    )
    assert "404" in page.inner_text("#status")
    # Other layers are unaffected.
    page.evaluate(f"setLayer({json.dumps(KEYS[0])}, true)")
    page.wait_for_function(
        f"MANIFEST.chunks.find(c => c.baseKey === {json.dumps(KEYS[0])}).key in states && Object.values(states).some(s => s.loaded)"
    )
    assert page.is_visible("#retryFailed")
    page.click(".layer-status.error button.retry")
    page.wait_for_function(
        f"states[MANIFEST.chunks.find(c => c.baseKey === {json.dumps(key)}).key].loaded"
    )
    assert page.query_selector(".layer-status.error") is None
    assert page.is_hidden("#retryFailed")
    # The retry bypassed the HTTP cache.
    assert len(attempts) == 2
    # The retry asked the browser to bypass its HTTP cache; the first try did not.
    name = chunk_name.rsplit("/", 1)[-1]
    modes = page.evaluate(
        "name => window.__cacheModes.filter(([url]) => url.endsWith(name)).map(([, mode]) => mode)",
        name,
    )
    assert modes == ["default", "reload"]


def test_a_corrupt_chunk_is_reported_and_retry_refetches_a_good_copy(
    watched_page, served, lazy_scene_dir
):
    page = open_lazy(watched_page, served, lazy_scene_dir)
    key = KEYS[3]
    url = page.evaluate(
        f"MANIFEST.chunks.find(c => c.baseKey === {json.dumps(key)}).url"
    )
    calls = []

    def handler(route):
        calls.append(1)
        if len(calls) == 1:
            response = route.fetch()
            body = bytearray(response.body())
            body[-1] ^= 0xFF
            route.fulfill(response=response, body=bytes(body))
        else:
            route.continue_()

    page.route(f"**/{url.rsplit('/', 1)[-1]}", handler)
    page.evaluate(f"setLayer({json.dumps(key)}, true)")
    page.wait_for_selector(".layer-status.error")
    assert "sha256" in page.inner_text("#status")
    assert page.evaluate("Object.values(states).every(s => !s.loaded)")
    page.click("#retryFailed")
    page.wait_for_function("Object.values(states).some(s => s.loaded)")
    assert page.query_selector(".layer-status.error") is None
    assert len(calls) == 2


def test_gpu_budget_evicts_hidden_layers_least_recently_shown_first(
    watched_page, served, lazy_scene_dir
):
    page = open_lazy(watched_page, served, lazy_scene_dir)
    page.uncheck("#releaseHidden")
    a, b, c = KEYS[:3]
    for key in (a, b, c):  # shown in this order, so `a` is the oldest
        page.evaluate(f"setLayer({json.dumps(key)}, true)")
        page.wait_for_function(
            f"states[MANIFEST.chunks.find(c => c.baseKey === {json.dumps(key)}).key].loaded"
        )
    for key in (a, b, c):
        page.evaluate(f"setLayer({json.dumps(key)}, false)")
    loaded = page.evaluate("Object.values(states).filter(s => s.loaded).length")
    assert loaded == 3  # hidden but kept: "unload hidden" is off
    bytes_c = page.evaluate(
        f"(() => {{ const s = states[MANIFEST.chunks.find(c => c.baseKey === {json.dumps(c)}).key]; return s.buffers.count * GPU_BYTES_PER_VERTEX; }})()"
    )
    page.fill("#gpuBudget", str(bytes_c / 2**20))
    page.dispatch_event("#gpuBudget", "change")

    def is_loaded(key):
        return page.evaluate(
            f"states[MANIFEST.chunks.find(c => c.baseKey === {json.dumps(key)}).key].loaded"
        )

    # Oldest evicted until the budget fits: only the most recently shown stays.
    assert [is_loaded(a), is_loaded(b), is_loaded(c)] == [False, False, True]
    assert page.evaluate("gpuBytes()") <= bytes_c


def test_visible_layers_are_never_evicted_and_the_overrun_is_reported(
    watched_page, served, lazy_scene_dir
):
    page = open_lazy(watched_page, served, lazy_scene_dir)
    page.fill("#gpuBudget", "0.0001")
    page.dispatch_event("#gpuBudget", "change")
    key = json.dumps(KEYS[0])
    page.evaluate(f"setLayer({key}, true)")
    page.wait_for_function(
        f"states[MANIFEST.chunks.find(c => c.baseKey === {key}).key].loaded"
    )
    assert page.evaluate(
        f"states[MANIFEST.chunks.find(c => c.baseKey === {key}).key].visible"
    )
    assert "over the" in page.inner_text("#status")


def test_gpu_estimate_matches_the_loaded_vertices(watched_page, served, lazy_scene_dir):
    page = open_lazy(watched_page, served, lazy_scene_dir)
    page.click("#loadAll")
    page.wait_for_function("Object.values(states).every(s => s.loaded)")
    vertices = page.evaluate(
        "Object.values(states).reduce((n, s) => n + s.buffers.count, 0)"
    )
    assert page.evaluate("gpuBytes()") == vertices * 21
    assert page.inner_text("#loadedGpu").split()[0] != "0"


@pytest.mark.parametrize("fail_status", [500])
def test_failures_do_not_block_other_layers_in_a_bulk_load(
    watched_page, served, lazy_scene_dir, fail_status
):
    page = open_lazy(watched_page, served, lazy_scene_dir)
    broken = page.evaluate(
        f"MANIFEST.chunks.find(c => c.baseKey === {json.dumps(KEYS[1])}).url"
    ).rsplit("/", 1)[-1]
    page.route(
        f"**/{broken}", lambda route: route.fulfill(status=fail_status, body="boom")
    )
    page.evaluate("maxFetches = 2")
    start_all(page)
    wait_idle(page)
    loaded = page.evaluate("Object.values(states).filter(s => s.loaded).length")
    assert loaded == len(KEYS) - 1
    assert page.evaluate("Object.values(states).filter(s => s.error).length") == 1
    assert "1 failed" in page.inner_text("#loadProgress")
