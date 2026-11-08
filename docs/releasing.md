# Releasing

This project uses Semantic Versioning, pre-1.0 (see `CHANGELOG.md`'s
"Package version" section). `pyproject.toml`'s `version` field is the one
authoritative value; `gdml_inspector.__version__` is always derived from
the installed distribution, never a second hand-maintained literal.

## Preparing a release

1. **Add a `## [X.Y.Z]` section to `CHANGELOG.md` by hand**, describing the
   user-facing changes, and commit it to `main`. This is a hard
   prerequisite, not a suggestion: the release commit itself (the next
   step) is restricted to exactly `pyproject.toml` and `uv.lock`, so the
   CHANGELOG entry must already exist for the publish workflow to find it.
2. From an up-to-date, clean `main` checkout with `uv sync --locked`
   already run:
   ```bash
   uv run --no-sync python scripts/release.py {major|minor|patch}
   uv run --no-sync python scripts/check_release.py
   ```
   `release.py` bumps the version (via `bump-my-version`), regenerates
   `uv.lock`, and creates a commit ("Release: X.Y.Z") and an annotated tag
   ("vX.Y.Z") locally. **It never pushes.** `check_release.py` is a
   read-only second opinion confirming the result is well-formed; review
   `git show`/`git log` yourself too.
3. Push, reviewed, with both refs landing together:
   ```bash
   git push --atomic origin HEAD:refs/heads/main refs/tags/vX.Y.Z
   ```

Alternatively, trigger the **Release** GitHub Actions workflow
(`workflow_dispatch`, choose the version part) instead of steps 2-3
locally — it runs the same two scripts, then pushes the commit and tag
itself.

## What happens after the tag is pushed

Pushing a `vX.Y.Z` tag (by either method above) triggers the **Publish
release** workflow automatically. It independently re-validates the
release (a fresh checkout, not trusting whatever pushed the tag), builds
the wheel and sdist, runs the same install/CLI smoke checks as ordinary CI
against the built artifacts, and creates a GitHub Release with both files
attached — using the matching `CHANGELOG.md` section as the release notes
(falling back to GitHub's auto-generated notes, with a visible warning, if
no matching section exists).

Normal development CI (`ci.yml`, `mypy.yml`) never runs these scripts or
touches `gh release` — publication only ever happens via one of the two
routes above, both of which require a human to explicitly choose a version
part or push a tag.

PyPI and container-registry publication are explicit future destinations,
not configured here; see issue #22 and the `Docker distribution` issue
(#23) for that follow-up work.

## If something goes wrong

- **`check_release.py` reports a problem locally**: fix the underlying
  issue (e.g. re-run with a clean tree, or on `main`) and start over —
  nothing has been pushed yet.
- **The tag was already pushed, but publication itself failed** (e.g. a
  transient build failure): re-run only the **Publish release** workflow
  for that tag — `gh workflow run publish-release.yml --ref vX.Y.Z` — no
  need to redo the commit/tag step.
- **A tag or release already exists for the version you're trying to
  prepare**: `release.py` refuses outright rather than overwriting
  anything; delete the stray local tag (`git tag -d vX.Y.Z`) if it was
  left over from an earlier, discarded attempt, or check whether that
  version was genuinely already released before trying again.
