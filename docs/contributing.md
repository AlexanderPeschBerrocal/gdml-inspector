# Contributing

## Branches and review

Work on each issue in a dedicated branch, for example
`issue-14-locked-development-environment`. Commit changes on that branch and
merge through a reviewed pull request instead of committing directly to `main`.
Publishing a branch or opening a PR is a separate, explicit step.

Use a clear commit subject and a body explaining the problem, what changed,
why that approach was chosen, and relevant verification. Reference the issue.
PR descriptions should explain the final behaviour and validation for someone
who has not read the development conversation.

## Reproducible environment

The reference toolchain uses uv **0.13.6** and Python **3.12.16**. Install that
uv release using the [official installation instructions](https://docs.astral.sh/uv/getting-started/installation/)
or, in an environment used for Python tools:

```bash
python -m pip install 'uv==0.13.6'
```

From the repository root:

```bash
uv sync --locked
uv run --no-sync pytest
uv run --no-sync python -m ruff check .
uv run --no-sync python -m ruff format --check .
uv run --no-sync python -m build --no-isolation
```

uv uses `.python-version` to select the development interpreter, downloading it
if needed, and installs the project into `.venv` in editable mode. The committed
`uv.lock` records the resolved dependency versions and hashes, including optional
dependencies; the optional backend is not installed by a default sync.
`--locked` rejects stale project/lock metadata. `--no-sync` runs checks against
the environment just installed instead of changing it implicitly.

The interpreter pin is a local development choice. `requires-python = ">=3.10"`
remains the public compatibility declaration. CI explicitly overrides the pin
to exercise released Python 3.10–3.14 interpreters, including the development
patch release. The universal lock may select different compatible dependency
versions for different interpreters; one exact dependency version need not work
across every Python release. Additional interpreter versions should be verified
before adding CI coverage.

## Dependency groups and the optional backend

Only `pyg4ometry` is a published optional runtime extra. The base package has no
runtime dependencies. Development tools are local dependency groups:

| Group | Purpose |
| --- | --- |
| `test` | pytest, pytest-cov, Hypothesis, and their resolved dependencies |
| `lint` | the project's pinned Ruff release |
| `typing` | the project's pinned Mypy release, plus typing-extensions |
| `hooks` | pre-commit and the `lint` group |
| `build` | distribution tooling and pinned setuptools/wheel |
| `dev` | includes `test`, `lint`, `typing`, `build`, and `hooks`; installed by default |

Install only a particular group:

```bash
uv sync --locked --no-default-groups --group test
uv run --no-sync pytest

uv sync --locked --no-default-groups --group lint
uv run --no-sync python -m ruff check .
uv run --no-sync python -m ruff format --check .

uv sync --locked --no-default-groups --group typing
uv run --no-sync mypy

uv sync --locked --no-default-groups --group build
uv run --no-sync python -m build --no-isolation
```

A normal sync removes packages not selected for that environment. Run
`uv sync --locked` to restore the full development environment after switching
groups. Documentation, browser, and release groups should be added when their
respective tooling is introduced, using the same independent-group pattern.
They are not empty placeholders or unused dependencies today.

For the optional backend:

```bash
uv sync --locked --extra pyg4ometry
uv run --no-sync gdml-inspector examples/detector.gdml --backend pyg4ometry
```

That extra may need native dependencies and is subject to pyg4ometry's own
interpreter/platform support. A universal resolution is not a guarantee of a
prebuilt wheel on every platform. Dedicated backend CI is tracked separately
in [#24](https://github.com/AlexanderPeschBerrocal/gdml-inspector/issues/24).

## Updating dependencies and build tools

```bash
uv lock --check
uv lock --upgrade
uv lock --upgrade-package pytest
uv sync --locked
```

Use one of the two upgrade commands for the intended scope, review the lock
diff, and commit `pyproject.toml` and `uv.lock` together when metadata changes.
Ordinary dependency resolution should use the public PyPI index; do not commit
machine-specific mirror URLs, credentials, or local paths into the lock.

Ruff, setuptools, and wheel have explicit project pins. To upgrade a pinned
tool, update its declaration first, regenerate the lock, and run the affected
checks. setuptools/wheel pins occur both in the `build` group and
`tool.uv.build-constraint-dependencies`; keep those values aligned. The build
constraints also control isolated uv builds of the editable package. The
documented distribution command runs `python -m build --no-isolation` inside
the synced environment so its interpreter and complete build environment come
from the selected Python version and lock.

The uv version is fixed in `tool.uv.required-version`, the CI setup step, and
these installation instructions. Upgrade them together and regenerate/verify
the lock with the new uv release. Update `.python-version`, the corresponding
CI matrix entry, and these instructions together when changing the development
interpreter. Neither change should silently narrow public Python support.

## Updating the package version

`pyproject.toml`'s `version` field is the one authoritative value;
`gdml_inspector.__version__` is always derived from it via the installed
distribution's metadata, not a second literal. `tests/test_version.py`
fails if your local install falls out of sync with it, and the CI
isolated-wheel check verifies the same equality from an installed wheel
outside the checkout. See [CHANGELOG.md](../CHANGELOG.md)'s "Package
version" section for the versioning policy (Semantic Versioning, pre-1.0),
and [releasing.md](releasing.md) for the actual release process —
`scripts/release.py`/`scripts/check_release.py` own bumping the version,
regenerating `uv.lock`, and preparing the release commit/tag; don't edit
`pyproject.toml`'s version by hand outside of that process.

## Independent pip installations

The lock is for repository development/CI. Users installing a wheel or running
`python -m pip install .` resolve the package's public compatibility metadata;
they do not need uv or its lockfile. The public build-system ranges remain
independent of the reference uv build-tool pins.

For development without uv, use pip 25.1 or newer for dependency-group support:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade 'pip>=25.1'
python -m pip install -e . --group dev
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m build --no-isolation
```

This reads the group declarations but resolves versions independently of
`uv.lock`. The former `.[dev]` and `.[test]` extras have been replaced by groups.
End users needing meshing support can still use `pip install '.[pyg4ometry]'`.

## Formatting, lint, and commit hooks

Python formatting and lint share `pyproject.toml` and the Ruff version pinned in
the `lint` group and resolved in `uv.lock`. Ruff targets Python 3.10, uses
88-character lines, double quotes, spaces, and LF endings. The selected checks
cover basic syntax/errors, undefined/unused names, import ordering (`I`),
Python-version-aware modernisation (`UP`), and bug-prone patterns (`B`).
Unsafe automatic fixes are not enabled.

After `uv sync --locked`, enable hooks for this checkout:

```bash
uv run --no-sync pre-commit install
uv run --no-sync pre-commit run --all-files
```

For only the hook tooling, use
`uv sync --locked --no-default-groups --group hooks` first. That group includes
Ruff, so it is sufficient for both local hooks. Installation creates a local
Git hook; it is opt-in for each clone. The hook configuration is committed.

The hooks run lint with safe fixes before formatting. They are repository-local
system hooks and invoke `uv run --no-sync --offline python -m ruff`, using the
selected project Python's installed Ruff. They do not install an independent
Ruff environment or sync/build the project during a commit. uv must be on PATH,
including when Git is launched from an editor. Pip-only contributors can use
the manual commands below; these hook entries specifically require uv.

To check without installing hooks, use the same non-mutating commands as CI:

```bash
uv run --no-sync python -m ruff check .
uv run --no-sync python -m ruff format --check .
```

To fix locally:

```bash
uv run --no-sync python -m ruff check --fix .
uv run --no-sync python -m ruff format .
```

`exports/`, `build/`, and `dist/` are excluded from Ruff and the hook file
selection. Ruff's default exclusions also cover virtual environments and cache
directories. Forced exclusion applies even to an explicitly supplied generated
file. CI only checks; it never fixes files or commits formatting changes.

If a hook changes a file, the commit stops: inspect `git diff`, stage the
intended changes again, and retry. Fix any remaining lint errors manually.
After updating the lock or Ruff pin, run `uv sync --locked` again. If a hook
cannot find uv, Python, or the Ruff module, repair PATH and run that sync outside
the hook; offline hook execution intentionally does not repair environments.
Use `uv run --no-sync pre-commit uninstall` to remove the installed local hook.

## Compatibility and distribution checks

The [testing guide](testing.md) describes the numerical reference expectations,
deterministic generated cases, known expected failures, and coverage baseline.
For a local package/branch coverage report:

```bash
uv run --no-sync pytest --cov=gdml_inspector --cov-report=term-missing --cov-report=xml --cov-report=html
```

CI uploads XML/HTML reports per interpreter. Coverage has no arbitrary minimum
threshold; expected failures do not imply the corresponding geometry is correct.

To exercise another interpreter, override the development pin explicitly:

```bash
uv sync --locked --python 3.10
uv run --no-sync --python 3.10 pytest
uv sync --locked
```

CI checks the lock, installs the locked development groups, runs tests and
non-mutating lint/format checks, and builds distributions for each matrix
interpreter. It then installs the
wheel independently into a fresh environment outside the checkout, checks its
dependency metadata, and exports both standalone and lazy viewers. That check
also ensures development groups have not become runtime requirements.
