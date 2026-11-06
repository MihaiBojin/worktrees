"""Deletion permissions, approved identities and partial outcomes against real Git."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import sys

import pytest
from conftest import env_for
from test_cli import run, run_on_a_terminal_at

from worktrees import cli, prune
from worktrees.git import Refused


def stash(world, path, subject="pending") -> str:
    (path / "README").write_text(subject)
    world.git("stash", "push", "-qm", subject, at=path)
    return world.git("rev-parse", "refs/stash")


@pytest.mark.parametrize("entry", ["gwp", "gwr", "gwbd"])
@pytest.mark.parametrize(
    "values",
    [
        ("empty", "stashes"),
        ("stashes", "empty"),
        ("all",),
        ("empty", "empty", "stashes"),
    ],
)
def test_permissions_combine_without_changing_empty_verdict(world, entry, values):
    path = world.worktree("topic")
    sha = stash(world, path)
    if entry == "gwbd":
        world.git("worktree", "remove", str(path))
    args = [] if entry == "gwp" else ["topic"]
    for value in values:
        args += ["--delete", value]
    p = run(world, entry, *args, "--yes", "--json")
    assert p.returncode == 0, p.stderr
    assert not path.exists()
    assert "topic" not in world.git("branch", "--format=%(refname:short)").splitlines()
    assert not world.git("stash", "list")
    assert sha in p.stderr
    if entry == "gwbd":
        assert json.loads(p.stdout)["verdicts"][0]["verdict"] == "empty"


@pytest.mark.parametrize(
    "args",
    [
        ("gwp", "--delete", "wat"),
        ("gwbd", "--delete", "ignored-files"),
        ("gwbs", "--delete", "all"),
        *[
            ("gwr", *flags)
            for value in ("empty", "stashes", "all")
            for flags in (
                ("--keep-branch", "--delete", value, "--yes"),
                ("--delete", value, "--keep-branch"),
            )
        ],
    ],
)
def test_invalid_permission_is_usage_before_repository_preflight(world, args):
    path = world.merged_worktree("topic")
    world.git("config", "remote.origin.promisor", "true")
    p = run(world, *args)
    assert p.returncode == 2, p.stderr
    assert path.exists()
    assert world.git("rev-parse", "refs/heads/topic")


@pytest.mark.parametrize("kind", ["empty", "unfinished", "finished", "main", "master"])
def test_keep_branch_keeps_ref_and_stashes_even_when_protected(world, kind):
    path = (
        world.merged_worktree("topic")
        if kind == "finished"
        else world.worktree("topic")
    )
    if kind == "unfinished":
        world.commit("unfinished", at=path)
    if kind in ("main", "master"):
        if kind == "main":
            world.git("branch", "-m", "main", "trunk")
        world.git("branch", "-m", "topic", kind, at=path)
    branch = kind if kind in ("main", "master") else "topic"
    tip = world.git("rev-parse", "HEAD", at=path)
    sha = stash(world, path)
    p = run(world, "gwr", str(path), "--keep-branch", "--yes", "--quiet")
    assert p.returncode == 0, p.stderr
    assert not path.exists()
    assert world.git("rev-parse", f"refs/heads/{branch}") == tip
    assert world.git("rev-parse", "refs/stash") == sha
    assert "retained" in p.stderr
    assert f"git branch {branch} {tip}" in p.stderr
    assert "git stash store -m" in p.stderr


@pytest.mark.parametrize("entry", ["gwp", "gwr", "gwbd"])
def test_yes_does_not_grant_permissions(world, entry):
    path = world.worktree("topic")
    sha = stash(world, path)
    if entry == "gwbd":
        world.git("worktree", "remove", str(path))
    args = [] if entry == "gwp" else ["topic"]
    p = run(world, entry, *args, "--yes")
    assert p.returncode in (0, 1, 3)
    assert world.git("rev-parse", "refs/heads/topic")
    assert world.git("rev-parse", "refs/stash") == sha


@pytest.mark.parametrize(
    "change",
    [
        "commit",
        "switch",
        "head",
        "dirty",
        "lock",
        "stash",
        "ignored",
        "ignored-replaced",
        "moved",
    ],
)
def test_recheck_refuses_changed_approved_state(world, monkeypatch, capsys, change):
    world.commit(".gitignore", "*.secret\n")
    path = world.merged_worktree("topic")
    (path / "old.secret").write_text("old")
    tip = world.git("rev-parse", "topic")
    moved = world.root / "moved"

    def confirm(*args, **kwargs):
        if change == "commit":
            world.commit("new", at=path)
        elif change == "switch":
            world.git("checkout", "-qb", "other", at=path)
        elif change == "head":
            world.commit("head-moved")
        elif change == "dirty":
            (path / "README").write_text("dirty")
        elif change == "lock":
            world.git("worktree", "lock", str(path))
        elif change == "stash":
            stash(world, path)
        elif change.startswith("ignored"):
            (path / "new.secret").write_text("new")
            if change == "ignored-replaced":
                (path / "old.secret").unlink()
        else:
            world.git("worktree", "move", str(path), str(moved))
        return True

    monkeypatch.setattr(prune, "confirm", confirm)
    assert cli.gwr([str(path), "--delete", "all"]) != 0
    output = capsys.readouterr()
    assert str(path) in output.err
    assert f"git branch topic {tip}" in output.err
    assert path.exists() or moved.exists()
    assert world.git("rev-parse", "topic")


@pytest.mark.parametrize("query", ["top", "tpc", "missing"])
def test_remove_never_fuzzy_matches(world, query):
    path = world.merged_worktree("topic")
    p = run(world, "gwr", query, "--yes")
    assert p.returncode != 0
    assert path.exists()


def test_duplicate_display_names_refuse_with_paths(world):
    paths = []
    for prefix in ("a", "b"):
        path = world.merged_worktree(prefix)
        dest = world.root / prefix / "same"
        dest.parent.mkdir(parents=True)
        world.git("worktree", "move", str(path), str(dest))
        paths.append(dest)
    p = run(world, "gwr", "same", "--yes")
    assert p.returncode != 0
    for path in paths:
        assert path.exists()
        assert str(path) in p.stderr


def test_current_worktree_needs_second_terminal_confirmation(world):
    path = world.merged_worktree("topic")
    p = run(world, "gwr", "topic", "--yes", at=path)
    assert p.returncode != 0
    assert path.exists()
    code, output = run_on_a_terminal_at(
        world, path, "gwr", "topic", "--yes", answer="n\n"
    )
    assert code == 0, output
    assert path.exists()
    assert str(path) in output
    code, output = run_on_a_terminal_at(
        world, path, "gwr", "topic", "--yes", answer="y\n"
    )
    assert code == 0, output
    assert not path.exists()
    assert output.rstrip().endswith(str(world.repo))


@pytest.mark.parametrize("step", ["checkout", "ref"])
def test_failed_step_preserves_later_data_and_reports_actual_outcomes(
    world, monkeypatch, capsys, step
):
    path = world.merged_worktree("topic")
    sha = stash(world, path)

    def refuse(*args, **kwargs):
        raise Refused("injected refusal")

    monkeypatch.setattr(
        prune, "worktree_remove" if step == "checkout" else "ref_delete", refuse
    )
    assert cli.gwr([str(path), "--delete", "stashes", "--yes", "--json"]) != 0
    output = capsys.readouterr()
    assert path.exists() == (step == "checkout")
    assert world.git("rev-parse", "topic")
    assert world.git("rev-parse", "refs/stash") == sha
    assert "topic" in output.err
    result = json.loads(output.out)
    assert bool(result["removed"]) == (step == "ref")


def test_remaining_duplicate_checkout_blocks_ref_deletion(world):
    path = world.merged_worktree("topic")
    other = world.root / "other"
    world.git("worktree", "add", "--force", str(other), "topic")
    p = run(world, "gwr", str(path), "--yes")
    assert p.returncode != 0
    assert not path.exists()
    assert other.exists()
    assert world.git("rev-parse", "topic")


@pytest.mark.parametrize("entry", ["gwr", "gwbd"])
def test_stash_drop_repairs_a_real_concurrent_insertion(world, entry):
    path = world.merged_worktree("topic")
    target = stash(world, path, "target 'quoted'")
    if entry == "gwbd":
        world.git("worktree", "remove", str(path))
    binary = world.root / "bin"
    binary.mkdir()
    real = shutil.which("git")
    wrapper = binary / "git"
    wrapper.write_text(
        f"#!{sys.executable}\nimport os, subprocess, sys\n"
        f"real = {real!r}\na = sys.argv[1:]\n"
        "if 'stash' in a and 'drop' in a:\n"
        f"    open({str(world.repo / 'README')!r}, 'w').write('concurrent')\n"
        f"    subprocess.run([real, '-C', {str(world.repo)!r}, "
        "'stash', 'push', '-qm', 'racing subject'], check=True)\n"
        "os.execv(real, [real, *a])\n"
    )
    wrapper.chmod(0o755)
    import subprocess

    env = env_for(world)
    p = subprocess.run(
        [
            sys.executable,
            "-c",
            f"from worktrees.cli import {entry}; raise SystemExit({entry}())",
            "topic",
            "--delete",
            "stashes",
            "--yes",
            "--json",
        ],
        cwd=world.repo,
        env={**env, "PATH": f"{binary}{os.pathsep}{env['PATH']}"},
        capture_output=True,
        text=True,
    )
    assert p.returncode != 0, p.stderr
    entries = world.git("stash", "list", "--format=%H %gs").splitlines()
    assert len(entries) == 2
    assert any(line.startswith(target) for line in entries)
    assert any("On main: racing subject" in line for line in entries)
    assert "not dropped" in p.stderr
    subject = shlex.quote("On topic: target 'quoted'")
    assert f"git stash store -m {subject} {target}" in p.stderr


@pytest.mark.parametrize("entry", ["gwr", "gwbd"])
def test_equal_sha_symbolic_head_retarget_refuses(world, monkeypatch, capsys, entry):
    path = world.merged_worktree("topic")
    world.git("remote", "add", "origin", "https://example.invalid/repo")
    for branch in ("main", "trunk"):
        world.git("update-ref", f"refs/remotes/origin/{branch}", "main")
    world.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    if entry == "gwbd":
        world.git("worktree", "remove", str(path))

    def change(*args, **kwargs):
        world.git(
            "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk"
        )
        return "y"

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(prune, "_prompt", change)
    assert getattr(cli, entry)(["topic"]) != 0
    assert world.git("rev-parse", "topic")
    assert "head branch changed" in capsys.readouterr().err


@pytest.mark.parametrize("change", ["commit", "stash", "occupied", "symbolic"])
def test_loose_branch_rechecks_approved_state(world, monkeypatch, change):
    path = world.merged_worktree("topic")
    world.git("worktree", "remove", str(path))

    def mutate(*args, **kwargs):
        if change == "commit":
            world.git("update-ref", "refs/heads/topic", "main")
        elif change == "symbolic":
            world.git("branch", "other", "topic")
            world.git("symbolic-ref", "refs/heads/topic", "refs/heads/other")
        else:
            world.git("worktree", "add", str(path), "topic")
            if change == "stash":
                stash(world, path)
                world.git("worktree", "remove", str(path))
        return "y"

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(prune, "_prompt", mutate)
    assert cli.gwbd(["topic", "--delete", "all"]) != 0
    assert world.git("rev-parse", "topic")


def test_stashes_disappearing_after_ref_delete_are_skipped(world, monkeypatch):
    path = world.merged_worktree("topic")
    stash(world, path)
    actual = prune.ref_delete

    def delete(*args, **kwargs):
        result = actual(*args, **kwargs)
        world.git("stash", "drop", "stash@{0}")
        return result

    monkeypatch.setattr(prune, "ref_delete", delete)
    assert cli.gwr([str(path), "--delete", "stashes", "--yes"]) == 0
    assert not world.git("stash", "list")


def test_multiple_stashes_drop_highest_index_first(world, monkeypatch):
    path = world.merged_worktree("topic")
    stash(world, path, "first")
    unrelated = stash(world, world.repo, "unrelated")
    stash(world, path, "second")
    actual = prune.stash_drop
    selectors = []

    def drop(selector, **kwargs):
        selectors.append(selector)
        return actual(selector, **kwargs)

    monkeypatch.setattr(prune, "stash_drop", drop)
    assert cli.gwr([str(path), "--delete", "stashes", "--yes"]) == 0
    assert selectors == ["stash@{2}", "stash@{0}"]
    assert world.git("stash", "list", "--format=%H") == unrelated


@pytest.mark.parametrize("json_output", [False, True])
def test_current_partial_failure_reports_destination_and_nonzero(
    world, monkeypatch, capsys, json_output
):
    path = world.merged_worktree("topic")
    monkeypatch.chdir(path)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(prune, "_prompt", lambda _: "y")

    def refuse(*args, **kwargs):
        assert os.getcwd() == str(world.repo)
        raise Refused("ref deletion failed")

    monkeypatch.setattr(prune, "ref_delete", refuse)
    original_remove = prune.worktree_remove

    def remove_from_main(*args, **kwargs):
        assert os.getcwd() == str(world.repo)
        return original_remove(*args, **kwargs)

    monkeypatch.setattr(prune, "worktree_remove", remove_from_main)
    assert cli.gwr(["topic", "--yes", *(["--json"] if json_output else [])]) != 0
    output = capsys.readouterr()
    assert not path.exists()
    assert world.git("rev-parse", "topic")
    assert (
        json.loads(output.out)["path"] if json_output else output.out.strip()
    ) == str(world.repo)


def test_delete_all_keeps_protected_targets_and_neighbor(world):
    world.commit(".gitignore", "*.secret\n")
    finished = world.merged_worktree("finished")
    empty = world.worktree("empty")
    locked = world.merged_worktree("locked")
    dirty = world.merged_worktree("dirty")
    world.git("worktree", "lock", str(locked))
    (dirty / "README").write_text("dirty")
    neighbor = world.sibling("neighbor", "finished")
    world.git("branch", "master")
    protected = world.root / "master"
    world.git("worktree", "add", str(protected), "master")
    (finished / "allowed.secret").write_text("ignored")
    stash(world, finished)
    p = run(world, "gwp", "--delete", "all", "--yes")
    assert p.returncode == 0, p.stderr
    assert not finished.exists() and not empty.exists()
    assert all(
        path.exists() for path in (locked, dirty, neighbor, protected, world.repo)
    )
    assert world.git("rev-parse", "master")
    assert world.git("rev-parse", "main")


@pytest.mark.parametrize("entry", ["gwr", "gwbd"])
def test_disclosure_keeps_assessed_sha_when_ref_changes_before_printing(
    world, monkeypatch, capsys, entry
):
    path = world.merged_worktree("topic")
    tip = world.git("rev-parse", "topic")
    if entry == "gwbd":
        world.git("worktree", "remove", str(path))
    actual = prune.plan

    def changed(*args, **kwargs):
        world.git("update-ref", "refs/heads/topic", "main")
        actual(*args, **kwargs)

    monkeypatch.setattr(prune, "plan", changed)
    assert getattr(cli, entry)(["topic", "--yes", "--quiet", "--json"]) != 0
    output = capsys.readouterr()
    assert f"git branch topic {tip}" in output.err
    assert "Head: main (inferred" in output.err
    json.loads(output.out)


def test_duplicate_name_picker_excludes_partial_matches(world):
    paths = []
    for branch, leaf in (("first", "same"), ("second", "same"), ("third", "same-more")):
        path = world.merged_worktree(branch)
        dest = world.root / branch / leaf
        dest.parent.mkdir()
        world.git("worktree", "move", str(path), str(dest))
        paths.append(dest)
    code, output = run_on_a_terminal_at(
        world, world.repo, "gwr", "same", "--yes", answer="1"
    )
    assert code == 0, output
    assert str(paths[2]) not in output
    assert not paths[0].exists()
    assert paths[1].exists() and paths[2].exists()


@pytest.mark.parametrize("entry", ["gwp", "gwr", "gwbd"])
def test_never_synced_disclosure_and_confirmation_survive_output_flags(world, entry):
    path = world.merged_worktree("topic")
    world.git("remote", "add", "origin", "https://example.invalid/repo")
    world.git("update-ref", "refs/remotes/origin/main", "main")
    world.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    if entry == "gwbd":
        world.git("worktree", "remove", str(path))
    args = [] if entry == "gwp" else ["topic"]
    code, output = run_on_a_terminal_at(
        world, world.repo, entry, *args, "--quiet", "--json", answer="n\n"
    )
    assert code == 0, output
    assert "Never synced:" in output
    assert "Never synced. Delete anyway?" in output
    assert "git branch topic" in output
    assert world.git("rev-parse", "topic")


def test_keep_branch_still_requires_ignored_permission(world):
    world.commit(".gitignore", "*.secret\n")
    path = world.worktree("topic")
    (path / "private.secret").write_text("preserve")
    denied = run(world, "gwr", "topic", "--keep-branch", "--yes")
    assert denied.returncode != 0
    assert (path / "private.secret").read_text() == "preserve"
    allowed = run(
        world, "gwr", "topic", "--keep-branch", "--delete", "ignored-files", "--yes"
    )
    assert allowed.returncode == 0, allowed.stderr
    assert "private.secret" in allowed.stderr
    assert not path.exists()
    assert world.git("rev-parse", "topic")


def test_detached_removal_never_deletes_a_ref(world, monkeypatch):
    path = world.merged_worktree("topic")
    world.git("checkout", "--detach", at=path)

    def forbidden(*args, **kwargs):
        raise AssertionError("detached removal reached ref deletion")

    monkeypatch.setattr(prune, "ref_delete", forbidden)
    assert cli.gwr([str(path), "--yes"]) == 0
    assert not path.exists()
    assert world.git("rev-parse", "topic")


@pytest.mark.parametrize("entry", ["gwr", "gwbd"])
def test_partial_stash_failure_lists_all_retained_shas(
    world, monkeypatch, capsys, entry
):
    path = world.merged_worktree("topic")
    shas = [stash(world, path, "one"), stash(world, path, "two")]
    if entry == "gwbd":
        world.git("worktree", "remove", str(path))

    def refuse(*args, **kwargs):
        raise Refused("stash drop failed")

    monkeypatch.setattr(prune, "stash_drop", refuse)
    assert getattr(cli, entry)(["topic", "--delete", "stashes", "--yes", "--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    outcome = result["outcome"] if entry == "gwr" else result["outcomes"][0]
    assert outcome["branch_removed"]
    assert sorted(outcome["retained"]) == sorted(shas)
    assert sorted(world.git("stash", "list", "--format=%H").splitlines()) == sorted(
        shas
    )


def test_initialized_submodule_after_confirmation_is_preserved(world, monkeypatch):
    module = world.root / "module-source"
    world.git("init", "-qb", "main", str(module))
    world.commit("README", at=module)
    path = world.worktree("topic")
    world.git(
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        "--quiet",
        str(module),
        "module",
        at=path,
    )
    world.git("commit", "-qm", "module", at=path)
    world.git("merge", "--no-ff", "-qm", "merge", "topic")
    world.git("submodule", "deinit", "--all", at=path)

    def initialize(*args, **kwargs):
        world.git(
            "-c", "protocol.file.allow=always", "submodule", "update", "--init", at=path
        )
        return True

    monkeypatch.setattr(prune, "confirm", initialize)
    assert cli.gwr(["topic", "--delete", "all"]) != 0
    assert (path / "module" / "README").exists()
    assert world.git("rev-parse", "topic")


@pytest.mark.parametrize("entry", ["gwr", "gwbd"])
@pytest.mark.parametrize("name", ["main", "master", "trunk"])
def test_all_never_permits_protected_branch_deletion(world, entry, name):
    path = world.merged_worktree("topic")
    tip = world.git("rev-parse", "topic")
    world.git("branch", "master", tip)
    world.git("branch", "trunk")
    world.git("checkout", "-qb", "holding")
    world.git("update-ref", "refs/heads/main", tip)
    world.git("remote", "add", "origin", "https://example.invalid/repo")
    world.git("update-ref", "refs/remotes/origin/trunk", "holding")
    world.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
    if entry == "gwr":
        world.git("checkout", name, at=path)
    p = run(world, entry, name, "--delete", "all", "--yes")
    assert p.returncode != 0
    assert "protected branch" in p.stderr
    assert world.git("rev-parse", name)
    assert path.exists()


def test_no_query_preserves_single_candidate_selection(world):
    path = world.merged_worktree("topic")
    p = run(world, "gwr", "--yes")
    assert p.returncode == 0, p.stderr
    assert not path.exists()


@pytest.mark.parametrize("step", ["checkout", "ref"])
def test_real_git_refusal_preserves_later_cleanup(world, monkeypatch, capsys, step):
    path = world.merged_worktree("topic")
    sha = stash(world, path)
    if step == "checkout":
        original = prune.worktree_remove

        def raced(*args, **kwargs):
            world.git("worktree", "lock", str(path))
            return original(*args, **kwargs)

        monkeypatch.setattr(prune, "worktree_remove", raced)
    else:
        original = prune.ref_delete

        def raced(*args, **kwargs):
            world.git("update-ref", "refs/heads/topic", "main")
            return original(*args, **kwargs)

        monkeypatch.setattr(prune, "ref_delete", raced)
    assert cli.gwr(["topic", "--delete", "stashes", "--yes", "--json"]) == 1
    result = json.loads(capsys.readouterr().out)["outcome"]
    assert result["worktree_removed"] == (step == "ref")
    assert not result["branch_removed"]
    assert world.git("rev-parse", "topic")
    assert world.git("rev-parse", "refs/stash") == sha
    assert path.exists() == (step == "checkout")
