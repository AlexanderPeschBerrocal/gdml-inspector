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
uv run --no-sync ruff check .
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
| `test` | pytest and its resolved dependencies |
| `lint` | the project's pinned Ruff release |
| `build` | distribution tooling and pinned setuptools/wheel |
| `dev` | includes `test`, `lint`, and `build`; installed by default |

Install only a particular group:

```bash
uv sync --locked --no-default-groups --group test
uv run --no-sync pytest

uv sync --locked --no-default-groups --group lint
uv run --no-sync ruff check .

uv sync --locked --no-default-groups --group build
uv run --no-sync python -m build --no-isolation
```

A normal sync removes packages not selected for that environment. Run
`uv sync --locked` to restore the full development environment after switching
groups. Typing, documentation, browser, and release groups should be added when
their respective tooling is introduced, using the same independent-group
pattern. They are not empty placeholders or unused dependencies today.

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
python -m build --no-isolation
```

This reads the group declarations but resolves versions independently of
`uv.lock`. The former `.[dev]` and `.[test]` extras have been replaced by groups.
End users needing meshing support can still use `pip install '.[pyg4ometry]'`.

## Compatibility and distribution checks

To exercise another interpreter, override the development pin explicitly:

```bash
uv sync --locked --python 3.10
uv run --no-sync --python 3.10 pytest
uv sync --locked
```

CI checks the lock, installs the locked development groups, runs tests/lint,
and builds distributions for each matrix interpreter. It then installs the
wheel independently into a fresh environment outside the checkout, checks its
dependency metadata, and exports both standalone and lazy viewers. That check
also ensures development groups have not become runtime requirements.
