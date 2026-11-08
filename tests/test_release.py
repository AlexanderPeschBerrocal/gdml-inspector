"""scripts/release.py and scripts/check_release.py, exercised against a
disposable copy of this repository's current working tree (never the real
repo's own git history/remote) -- a fresh git history is built in tmp_path
so each test's commits/tags can never reach the real repo. Every rejection
case asserts the copy is left with zero trace: HEAD unchanged, clean
working tree, no stray tag."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
_EXCLUDE = {
    ".git",
    "exports",
    "dist",
    "build",
    "__pycache__",
    ".pytest_cache",
    ".venv",
    ".ruff_cache",
    ".mypy_cache",
}


def _ignore(_directory: str, names: list[str]) -> list[str]:
    return [n for n in names if n in _EXCLUDE or n.endswith(".egg-info")]


def _env() -> dict[str, str]:
    """PATH explicitly prefixed with this project's own .venv and the
    local uv install, so git/bump-my-version/uv resolve the same way
    regardless of how pytest itself was invoked."""
    env = dict(os.environ)
    search_path = [
        str(REPO_ROOT / ".venv" / "bin"),
        str(Path.home() / ".local" / "bin"),
        env.get("PATH", ""),
    ]
    env["PATH"] = os.pathsep.join(search_path)
    return env


@pytest.fixture
def release_repo(tmp_path) -> Path:
    import shutil

    repo = tmp_path / "repo"
    shutil.copytree(REPO_ROOT, repo, ignore=_ignore)

    def run(*args: str) -> None:
        subprocess.run(args, cwd=repo, env=_env(), check=True, capture_output=True)

    run("git", "init", "-q", "-b", "main")
    run("git", "config", "user.email", "test@example.invalid")
    run("git", "config", "user.name", "Test")
    run("git", "add", "-A")
    run("git", "commit", "-q", "-m", "Initial commit for release testing")
    return repo


def release(repo: Path, part: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(repo / "scripts" / "release.py"), part],
        cwd=repo,
        env=_env(),
        capture_output=True,
        text=True,
    )


def check_release(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(repo / "scripts" / "check_release.py")],
        cwd=repo,
        env=_env(),
        capture_output=True,
        text=True,
    )


def head(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()


def is_clean(repo: Path) -> bool:
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True
    ).stdout
    return status == ""


def tags(repo: Path) -> list[str]:
    return subprocess.run(
        ["git", "tag", "-l"], cwd=repo, capture_output=True, text=True
    ).stdout.split()


def test_release_then_check_release_succeeds_for_a_clean_patch_bump(release_repo):
    result = release(release_repo, "patch")
    assert result.returncode == 0, result.stderr

    check = check_release(release_repo)
    assert check.returncode == 0, check.stderr
    assert "valid release commit" in check.stdout

    assert (
        subprocess.run(
            ["git", "log", "-1", "--format=%s"],
            cwd=release_repo,
            capture_output=True,
            text=True,
        )
        .stdout.strip()
        .startswith("Release: ")
    )
    assert tags(release_repo)[-1].startswith("v")


def test_release_rejects_a_dirty_working_tree(release_repo):
    (release_repo / "README.md").write_text("dirty\n", encoding="utf-8")
    start = head(release_repo)

    result = release(release_repo, "patch")

    assert result.returncode != 0
    assert "working tree is not clean" in result.stderr
    assert head(release_repo) == start
    assert tags(release_repo) == []


def test_release_rejects_a_non_main_branch(release_repo):
    subprocess.run(
        ["git", "checkout", "-q", "-b", "not-main"],
        cwd=release_repo,
        env=_env(),
        check=True,
    )
    start = head(release_repo)

    result = release(release_repo, "patch")

    assert result.returncode != 0
    assert "must be run from main" in result.stderr
    assert head(release_repo) == start
    assert is_clean(release_repo)
    assert tags(release_repo) == []


def test_release_rejects_an_existing_tag_with_zero_trace(release_repo):
    first = release(release_repo, "patch")
    assert first.returncode == 0, first.stderr
    prepared_tag = tags(release_repo)[-1]

    # Simulate a previously prepared release whose commit was discarded
    # (e.g. after review) but whose tag was left dangling.
    subprocess.run(
        ["git", "reset", "--hard", "HEAD~1"], cwd=release_repo, env=_env(), check=True
    )
    start = head(release_repo)

    second = release(release_repo, "patch")

    assert second.returncode != 0
    assert f"tag {prepared_tag} already exists" in second.stderr
    assert head(release_repo) == start
    assert is_clean(release_repo)
    assert tags(release_repo) == [prepared_tag]


def test_check_release_detects_a_tampered_committed_version(release_repo):
    prepared = release(release_repo, "patch")
    assert prepared.returncode == 0, prepared.stderr
    prepared_tag = tags(release_repo)[-1]
    prepared_version = prepared_tag.removeprefix("v")

    # Tamper with the *committed* release: bump pyproject.toml's version
    # without touching uv.lock or the tag, breaking every cross-check.
    pyproject = release_repo / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    tampered = text.replace(f'version = "{prepared_version}"', 'version = "9.9.9"', 1)
    assert tampered != text, "fixture's version literal didn't match; update this test"
    pyproject.write_text(tampered, encoding="utf-8")
    subprocess.run(
        ["git", "commit", "-q", "-a", "--amend", "--no-edit"],
        cwd=release_repo,
        env=_env(),
        check=True,
    )

    result = check_release(release_repo)

    assert result.returncode != 0
    assert "uv.lock records gdml-inspector" in result.stderr
    assert "is not an annotated tag" in result.stderr
