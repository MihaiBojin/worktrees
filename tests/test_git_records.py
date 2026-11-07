"""Git's LF records and scalar names preserve non-LF characters."""

from __future__ import annotations

import json

import pytest
from test_cli import run


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\u0085", "\u00a0"])
@pytest.mark.parametrize("entry", ["gwp", "gwbd"])
def test_separator_branch_stashes_require_explicit_permission(world, separator, entry):
    name = "topic" + separator
    path = world.merged_worktree(name)
    (path / "README").write_text("unfinished")
    world.git("stash", "push", "-qm", "pending work here", at=path)
    sha = world.git("rev-parse", "refs/stash")
    if entry == "gwbd":
        world.git("worktree", "remove", str(path))
    preview = (
        run(world, "gwp", "--json")
        if entry == "gwp"
        else run(world, "gwbs", name, "--json")
    )
    assert preview.returncode == 0, preview.stderr
    row = next(v for v in json.loads(preview.stdout)["verdicts"] if v["branch"] == name)
    assert row["stashes"] == [sha]
    assert not row["eligible"]
    args = [] if entry == "gwp" else [name]
    denied = run(world, entry, *args, "--yes")
    assert denied.returncode in (0, 3), denied.stderr
    assert world.git("rev-parse", f"refs/heads/{name}")
    assert world.git("rev-parse", "refs/stash") == sha
    allowed = run(world, entry, *args, "--yes", "--delete", "stashes")
    assert allowed.returncode == 0, allowed.stderr
    assert not world.git("stash", "list")
    assert not path.exists()
    assert not world.git("branch", "--list", name)


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\u0085", "\u00a0"])
@pytest.mark.parametrize("entry", ["gwp", "gwbd"])
def test_exact_remote_head_counterpart_stays_protected(world, separator, entry):
    name = "topic" + separator
    path = world.merged_worktree(name)
    world.git("remote", "add", "origin", "https://example.invalid/repo")
    for branch in ("topic", name):
        world.git("update-ref", f"refs/remotes/origin/{branch}", "main")
    world.git("symbolic-ref", "refs/remotes/origin/HEAD", f"refs/remotes/origin/{name}")
    if entry == "gwbd":
        world.git("worktree", "remove", str(path))
    args = [] if entry == "gwp" else [name]
    preview = run(
        world,
        entry,
        *args,
        *(["--dry-run"] if entry == "gwbd" else []),
        "--delete",
        "all",
        "--json",
    )
    assert preview.returncode == 0, preview.stderr
    row = next(v for v in json.loads(preview.stdout)["verdicts"] if v["branch"] == name)
    assert not row["eligible"]
    assert "it is a protected branch" in row["protections"]
    removed = run(world, entry, *args, "--delete", "all", "--yes")
    assert removed.returncode in (0, 3), removed.stderr
    assert world.git("rev-parse", f"refs/heads/{name}")
    if entry == "gwp":
        assert path.exists()


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\u0085", "\u00a0"])
def test_configured_default_remote_name_is_exact(world, separator):
    name = "origin" + separator
    world.git("remote", "add", "origin", "https://example.invalid/wrong")
    world.git("remote", "add", name, "https://example.invalid/right")
    world.git("config", "checkout.defaultRemote", name)
    world.git("branch", "trunk")
    world.git("update-ref", f"refs/remotes/{name}/trunk", "trunk")
    world.git("symbolic-ref", f"refs/remotes/{name}/HEAD", f"refs/remotes/{name}/trunk")
    result = run(world, "gw", "list", "--json")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["head"]["ref"] == f"refs/remotes/{name}/HEAD"


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\u0085", "\u00a0"])
def test_move_renames_exact_branch_without_touching_trimmed_sibling(world, separator):
    name = "topic" + separator
    path = world.worktree(name)
    sibling = world.git("rev-parse", "main")
    world.git("branch", "topic", sibling)
    tip = world.commit("feature", at=path)
    moved = run(world, "gwm", "renamed", "--json", at=path)
    assert moved.returncode == 0, moved.stderr
    assert world.git("rev-parse", "topic") == sibling
    assert world.git("rev-parse", "renamed") == tip
    assert not world.git("branch", "--list", name)


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\u0085", "\u00a0"])
def test_branch_start_uses_exact_symbolic_head(world, separator):
    name = "topic" + separator
    before = world.git("rev-parse", "main")
    after = world.commit("new-head")
    world.git("remote", "add", "origin", "https://example.invalid/repo")
    world.git("update-ref", "refs/remotes/origin/topic", before)
    world.git("update-ref", f"refs/remotes/origin/{name}", after)
    world.git("symbolic-ref", "refs/remotes/origin/HEAD", f"refs/remotes/origin/{name}")
    result = run(world, "gwbc", "next", "--json")
    assert result.returncode == 0, result.stderr
    assert world.git("rev-parse", "next") == after
    assert json.loads(result.stdout)["base"] == f"refs/remotes/origin/{name}"


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\u0085", "\u00a0"])
def test_rotate_reads_exact_current_branch(world, separator):
    name = "topic" + separator
    path = world.worktree(name)
    world.git("branch", "topic")
    tip = world.commit("feature", at=path)
    result = run(world, "gwrot", "--json", at=path)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["previous"] == name
    assert world.git("rev-parse", f"refs/heads/{name}") == tip


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\u0085", "\u00a0"])
def test_plain_remote_head_names_the_one_branch_at_its_commit(world, separator):
    name = "trunk" + separator
    tip = world.commit("new-head")
    world.git("remote", "add", "origin", "https://example.invalid/repo")
    world.git("update-ref", f"refs/remotes/origin/{name}", tip)
    world.git("update-ref", "--no-deref", "refs/remotes/origin/HEAD", tip)
    result = run(world, "gwbc", "next", "--json")
    assert result.returncode == 0, result.stderr
    assert world.git("rev-parse", "next") == tip
    assert json.loads(result.stdout)["head"] == {
        "ref": "refs/remotes/origin/HEAD",
        "source": "clone",
    }
    listed = run(world, "gw", "list", "--list")
    assert f"Head: {name} (clone)" in listed.stderr


def test_plain_remote_head_at_two_branches_does_not_resolve(world):
    tip = world.commit("new-head")
    world.git("remote", "add", "origin", "https://example.invalid/repo")
    for branch in ("trunk", "release"):
        world.git("update-ref", f"refs/remotes/origin/{branch}", tip)
    world.git("update-ref", "--no-deref", "refs/remotes/origin/HEAD", tip)
    result = run(world, "gwbs", "--json")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["head"]["ref"] is None
    assert "Head: unresolved (clone). Run: gw sync" in run(world, "gwbs").stderr
    assert run(world, "gwbc", "next").returncode != 0
