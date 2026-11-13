# Benchmarks and stress testing

Issue #7 asks for numbers before optimisation, so this repository ships a
redistributable stress generator and a benchmark runner instead of committing
large detector files. Both live in `benchmarks/` (not in the installed wheel).

## Running

```bash
# Synthetic scenes (generated on the fly; nothing is committed)
python benchmarks/run_benchmarks.py --sizes small medium nested large

# Real detector files, with browser measurements (needs the browser group)
python benchmarks/run_benchmarks.py --gdml detector.gdml --browser --backend pyg4ometry

# Just generate a scene to export by hand
python benchmarks/stress_gdml.py stress.gdml --placements 20000 --logical 100
```

`stress_gdml.py` writes a deterministic GDML file: `--placements` objects
sharing `--logical` distinct logical volumes (so repetition is
placements / logical), in a `flat` or `nested` layout (`--group-size` children per
container), cycling boxes, tubes and cones with varying sizes, with names that
spread the objects over several lazy layers. To exercise one solid larger than a
chunk, export with `--chunk-vertices` below a tube's vertex count.

`run_benchmarks.py` exports each scenario in standalone and lazy mode, each in
its own process, with the caps raised so scene size rather than a cap limits the
run. `--repeat N` repeats each export (median time, highest RSS), `--json FILE`
keeps the raw results, `--browser-timeout` bounds how long a page may take to
load before it is recorded as "did not load".

## What is measured

| Column | Meaning |
| --- | --- |
| time (s) | wall time of the export process |
| peak RSS (MiB) | peak resident memory of that process (`wait4` `ru_maxrss`) |
| output (MiB) | bytes written: the HTML, plus for lazy the chunks, manifest assets and `placements.json` |
| initial transfer (MiB) | lazy only: the HTML plus the chunks the default layers fetch (`placements.json` is fetched on demand and not counted) |
| load (s) | browser: time until the page is usable (standalone: tree rendered; lazy: **Load all** finished) |
| fps | browser: frames per second while the camera rotates; **software WebGL here, see below** |
| JS heap (MiB) | browser: `performance.memory.usedJSHeapSize` after loading (approximate) |
| est. GPU (MiB) | lazy: loaded vertices x 21 B (position 12, colour 4, object id 4, visibility 1) |

## Baseline

The baseline below predates instancing: its lazy rows are what `--instancing off`
produces today.

Measured on one Linux machine (64 cores, 124 GB RAM, Python 3.12.16), exports
with `--quality fast`, browser metrics from headless Chromium using **software
WebGL (SwiftShader)**. The frame rates therefore describe the software rasteriser
and say nothing about a real GPU; compare runs on the same machine only. Time and
RSS are single runs. Regenerate with the commands above before comparing.

| scenario | mode | time (s) | peak RSS (MiB) | output (MiB) | initial transfer (MiB) | load (s) | fps | JS heap (MiB) | est. GPU (MiB) |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| synthetic-small (2,000 placements, 40 logical) | standalone | 0.8 | 98.1 | 9.9 | - | 1.0 | 23 | 42.6 | - |
| synthetic-small (2,000 placements, 40 logical) | lazy | 0.6 | 53.5 | 5.3 | 0.1 | 0.5 | 25 | 9.5 | 4.3 |
| synthetic-medium (20,000 placements, 100 logical) | standalone | 7.0 | 737.8 | 101.5 | - | 8.8 | 3 | 391.0 | - |
| synthetic-medium (20,000 placements, 100 logical) | lazy | 4.7 | 185.3 | 52.6 | 0.2 | 6.0 | 3 | 40.1 | 43.0 |
| synthetic-nested (20,000 placements, 100 logical) | standalone | 7.0 | 728.5 | 98.5 | - | 6.0 | 3 | 368.1 | - |
| synthetic-nested (20,000 placements, 100 logical) | lazy | 4.8 | 181.5 | 52.9 | 0.3 | 15.4 | 3 | 12.1 | 43.0 |
| synthetic-large (100,000 placements, 200 logical) | standalone | 37.1 | 3618.5 | 514.7 | - | did not load | - | - | - |
| synthetic-large (100,000 placements, 200 logical) | lazy | 23.2 | 786.5 | 264.0 | 0.3 | 79.2 | 1 | 51.0 | 215.9 |
| COCOA geantino scan (python backend, 7,405 placements) | standalone | 3.0 | 346.6 | 55.5 | - | 7.5 | 2 | 149.7 | - |
| COCOA geantino scan (python backend, 7,405 placements) | lazy | 1.9 | 105.1 | 20.7 | 0.2 | 3.6 | 3 | 20.7 | 13.2 |
| COCOA geantino scan (pyg4ometry, 7,405 placements) | standalone | 25.6 | 2268.0 | 317.4 | - | 15.0 | 1 | 852.6 | - |
| COCOA geantino scan (pyg4ometry, 7,405 placements) | lazy | 18.2 | 750.9 | 91.5 | 0.2 | 12.2 | 1 | 48.1 | 87.6 |

### Effect of instancing (`--instancing off` against the default `auto`)

Lazy exports, `--quality fast`, same machine and software WebGL as above.

| scene | instancing | output (MiB) | chunk bytes | est. GPU (MiB) | load (s) |
| --- | --- | ---: | ---: | ---: | ---: |
| synthetic-medium (20,000 placements, 100 logical) | off | 52.6 | 41.2 | 43.0 | 5.5 |
| synthetic-medium | auto | 14.7 | 3.2 | 3.0 | 3.2 |
| COCOA, python backend | off | 20.7 | 12.8 | 13.2 | 3.3 |
| COCOA, python backend | auto | 9.7 | 1.8 | 1.5 | 2.4 |
| COCOA, pyg4ometry backend (4.4 million vertices) | off | 91.5 | 83.6 | 87.6 | 12.8 |
| COCOA, pyg4ometry backend | auto | 11.2 | 3.3 | 3.2 | 3.5 |

(chunk bytes = output minus `placements.json`: 11.4 MiB for the synthetic scene,
7.9 MiB for COCOA.) Instancing shrinks the real detector's chunks about 25-fold
and its GPU buffers about 27-fold; what remains is `placements.json`, which now
dominates the output and is the next thing to split (slice 4). Frame rates on
software WebGL were no better (the synthetic scene went from 3 to 1 frames per
second): software rasterisation pays for the extra matrix work per vertex, so a
real GPU measurement is still needed before claiming a rendering speed-up.

### What the baseline shows

- **Repeated placements duplicate their triangles.** The 20,000-placement scenes
  share 100 logical volumes yet write 2.1 million vertices (about 43 MiB of lazy
  chunks); with one stored mesh per logical volume the geometry would be about
  200 times smaller. This is the case for instancing (slice 3 of #7).
- **Export memory grows with the whole scene.** Peak RSS scales roughly linearly
  with placements (lazy: 185 MiB at 20,000, 786 MiB at 100,000; standalone 738 MiB
  and 3.6 GiB) because every mesh is built in memory before anything is written
  (streaming preprocessing, slice 4).
- **The single-file export stops being usable around 10^5 placements.** The
  100,000-placement standalone page (515 MiB of HTML) did not load within 150 s;
  the lazy export of the same scene loads in about 80 s on software WebGL,
  with an initial transfer of 0.3 MiB.
- **`placements.json` is not small.** About 600 bytes per placement: 11 MiB at
  20,000 and 58 MiB at 100,000. It is fetched on demand (first pick, hierarchy
  or filter use), so it does not affect the initial transfer, but it will need
  per-chunk splitting for very large scenes.
- **Initial transfer is tiny** (0.1 to 0.4 MiB) in every lazy scenario because the
  default layers are small; loading everything transfers the whole output.
- The pyg4ometry backend gives the real detector about 6.6 times the vertices of
  the Python backend (4.4 million against 0.66 million), which dominates its cost.

Results from other machines should be added next to these with their hardware
named, not mixed into them.
