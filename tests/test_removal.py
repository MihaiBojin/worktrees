"""Deletion permissions, approved identities and partial outcomes against real Git."""

from __future__ import annotations

import json
import os
import pty
import re
import select
import shlex
import shutil
import subprocess
import sys
import time

import pytest
from conftest import argv, env_for
from test_branches import cli as subprocess_cli
from test_cli import run, run_on_a_terminal_at

from worktrees import cli, prune
from worktrees.git import Refused


def stash(world, path, subject="pending") -> str:
    (path / "README").write_text(subject)
    world.git("stash", "push", "-qm", subject, at=path)
    return world.git("rev-parse", "refs/stash")


@pytest.mark.parametrize("entry", ["gw prune", "gw remove", "gw branch delete"])
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
    if entry == "gw branch delete":
        world.git("worktree", "remove", str(path))
    args = [] if entry == "gw prune" else ["topic"]
    for value in values:
        args += ["--delete", value]
    p = run(world, entry, *args, "--yes", "--json")
    assert p.returncode == 0, p.stderr
    assert not path.exists()
    assert "topic" not in world.git("branch", "--format=%(refname:short)").splitlines()
    assert not world.git("stash", "list")
    assert sha in p.stderr
    if entry == "gw branch delete":
        assert json.loads(p.stdout)["verdicts"][0]["verdict"] == "empty"


@pytest.mark.parametrize(
    "args",
    [
        ("gw prune", "--delete", "wat"),
        ("gw branch delete", "--delete", "ignored-files"),
        ("gw branch status", "--delete", "all"),
        *[
            ("gw remove", *flags)
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
    p = run(world, "gw remove", str(path), "--keep-branch", "--yes", "--quiet")
    assert p.returncode == 0, p.stderr
    assert not path.exists()
    assert world.git("rev-parse", f"refs/heads/{branch}") == tip
    assert world.git("rev-parse", "refs/stash") == sha
    assert "retained" in p.stderr
    assert f"git branch {branch} {tip}" in p.stderr
    assert "git stash store -m" in p.stderr


@pytest.mark.parametrize("entry", ["gw prune", "gw remove", "gw branch delete"])
def test_yes_does_not_grant_permissions(world, entry):
    path = world.worktree("topic")
    sha = stash(world, path)
    if entry == "gw branch delete":
        world.git("worktree", "remove", str(path))
    args = [] if entry == "gw prune" else ["topic"]
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
    assert cli.gw(["remove", str(path), "--delete", "all"]) != 0
    output = capsys.readouterr()
    assert str(path) in output.err
    assert f"git branch topic {tip}" in output.err
    assert path.exists() or moved.exists()
    assert world.git("rev-parse", "topic")


@pytest.mark.parametrize("query", ["top", "tpc", "missing"])
def test_remove_never_fuzzy_matches(world, query):
    path = world.merged_worktree("topic")
    p = run(world, "gw remove", query, "--yes")
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
    p = run(world, "gw remove", "same", "--yes")
    assert p.returncode != 0
    for path in paths:
        assert path.exists()
        assert str(path) in p.stderr


def test_current_worktree_needs_second_terminal_confirmation(world):
    path = world.merged_worktree("topic")
    p = run(world, "gw remove", "topic", "--yes", at=path)
    assert p.returncode != 0
    assert path.exists()
    code, output = run_on_a_terminal_at(
        world, path, "gw remove", "topic", "--yes", answer="n\n"
    )
    assert code == 0, output
    assert path.exists()
    assert str(path) in output
    code, output = run_on_a_terminal_at(
        world, path, "gw remove", "topic", "--yes", answer="y\n"
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
    assert cli.gw(["remove", str(path), "--delete", "stashes", "--yes", "--json"]) != 0
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
    p = run(world, "gw remove", str(path), "--yes")
    assert p.returncode != 0
    assert not path.exists()
    assert other.exists()
    assert world.git("rev-parse", "topic")


@pytest.mark.parametrize("entry", ["gw remove", "gw branch delete"])
@pytest.mark.parametrize("suffix", ["", "\u2028"])
def test_stash_drop_repairs_a_real_concurrent_insertion(world, entry, suffix):
    path = world.merged_worktree("topic")
    target = stash(world, path, "target 'quoted'")
    if entry == "gw branch delete":
        world.git("worktree", "remove", str(path))
    binary = world.root / "bin"
    binary.mkdir()
    real = shutil.which("git")
    racing_subject = "racing subject" + suffix
    wrapper = binary / "git"
    wrapper.write_text(
        f"#!{sys.executable}\nimport os, subprocess, sys\n"
        f"real = {real!r}\na = sys.argv[1:]\n"
        "if 'stash' in a and 'drop' in a:\n"
        f"    open({str(world.repo / 'README')!r}, 'w').write('concurrent')\n"
        f"    subprocess.run([real, '-C', {str(world.repo)!r}, "
        f"'stash', 'push', '-qm', {racing_subject!r}], check=True)\n"
        "os.execv(real, [real, *a])\n"
    )
    wrapper.chmod(0o755)
    import subprocess

    env = env_for(world)
    p = subprocess.run(
        argv(entry, "topic", "--delete", "stashes", "--yes", "--json"),
        cwd=world.repo,
        env={**env, "PATH": f"{binary}{os.pathsep}{env['PATH']}"},
        capture_output=True,
        text=True,
    )
    assert p.returncode != 0, p.stderr
    entries = world.git("stash", "list", "--format=%H %gs").split("\n")
    assert len(entries) == 2
    assert any(line.startswith(target) for line in entries)
    assert any(f"On main: {racing_subject}" in line for line in entries)
    assert "not dropped" in p.stderr
    subject = shlex.quote("On topic: target 'quoted'")
    assert f"git stash store -m {subject} {target}" in p.stderr
    result = json.loads(p.stdout)
    outcome = result["outcome"] if entry == "gw remove" else result["outcomes"][0]
    assert outcome["retained"] == [target]
    assert outcome["skipped"] == []
    assert f"stash {target}: retained" in p.stderr


@pytest.mark.parametrize("entry", ["gw remove", "gw branch delete"])
def test_equal_sha_symbolic_head_retarget_refuses(world, monkeypatch, capsys, entry):
    path = world.merged_worktree("topic")
    world.git("remote", "add", "origin", "https://example.invalid/repo")
    for branch in ("main", "trunk"):
        world.git("update-ref", f"refs/remotes/origin/{branch}", "main")
    world.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    if entry == "gw branch delete":
        world.git("worktree", "remove", str(path))

    def change(*args, **kwargs):
        world.git(
            "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk"
        )
        return "y"

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(prune, "_prompt", change)
    assert cli.gw([*entry.split()[1:], "topic"]) != 0
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
    assert cli.gw(["branch", "delete", "topic", "--delete", "all"]) != 0
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
    assert cli.gw(["remove", str(path), "--delete", "stashes", "--yes"]) == 0
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
    assert cli.gw(["remove", str(path), "--delete", "stashes", "--yes"]) == 0
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
    assert (
        cli.gw(["remove", "topic", "--yes", *(["--json"] if json_output else [])]) != 0
    )
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
    p = run(world, "gw prune", "--delete", "all", "--yes")
    assert p.returncode == 0, p.stderr
    assert not finished.exists() and not empty.exists()
    assert all(
        path.exists() for path in (locked, dirty, neighbor, protected, world.repo)
    )
    assert world.git("rev-parse", "master")
    assert world.git("rev-parse", "main")


@pytest.mark.parametrize("entry", ["gw remove", "gw branch delete"])
def test_disclosure_keeps_assessed_sha_when_ref_changes_before_printing(
    world, monkeypatch, capsys, entry
):
    path = world.merged_worktree("topic")
    tip = world.git("rev-parse", "topic")
    if entry == "gw branch delete":
        world.git("worktree", "remove", str(path))
    actual = prune.plan

    def changed(*args, **kwargs):
        world.git("update-ref", "refs/heads/topic", "main")
        actual(*args, **kwargs)

    monkeypatch.setattr(prune, "plan", changed)
    assert cli.gw([*entry.split()[1:], "topic", "--yes", "--quiet", "--json"]) != 0
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
        world, world.repo, "gw remove", "same", "--yes", answer="\r"
    )
    assert code == 0, output
    assert str(paths[2]) not in output
    assert not paths[0].exists()
    assert paths[1].exists() and paths[2].exists()


@pytest.mark.parametrize("entry", ["gw prune", "gw remove", "gw branch delete"])
def test_never_synced_disclosure_and_confirmation_survive_output_flags(world, entry):
    path = world.merged_worktree("topic")
    world.git("remote", "add", "origin", "https://example.invalid/repo")
    world.git("update-ref", "refs/remotes/origin/main", "main")
    world.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    if entry == "gw branch delete":
        world.git("worktree", "remove", str(path))
    args = [] if entry == "gw prune" else ["topic", "--json"]
    code, output = run_on_a_terminal_at(
        world, world.repo, entry, *args, "--quiet", answer="n\n"
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
    denied = run(world, "gw remove", "topic", "--keep-branch", "--yes")
    assert denied.returncode != 0
    assert (path / "private.secret").read_text() == "preserve"
    allowed = run(
        world,
        "gw remove",
        "topic",
        "--keep-branch",
        "--delete",
        "ignored-files",
        "--yes",
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
    assert cli.gw(["remove", str(path), "--yes"]) == 0
    assert not path.exists()
    assert world.git("rev-parse", "topic")


@pytest.mark.parametrize("entry", ["gw remove", "gw branch delete"])
def test_partial_stash_failure_lists_all_retained_shas(
    world, monkeypatch, capsys, entry
):
    path = world.merged_worktree("topic")
    shas = [stash(world, path, "one"), stash(world, path, "two")]
    if entry == "gw branch delete":
        world.git("worktree", "remove", str(path))

    def refuse(*args, **kwargs):
        raise Refused("stash drop failed")

    monkeypatch.setattr(prune, "stash_drop", refuse)
    assert (
        cli.gw([*entry.split()[1:], "topic", "--delete", "stashes", "--yes", "--json"])
        == 1
    )
    result = json.loads(capsys.readouterr().out)
    outcome = result["outcome"] if entry == "gw remove" else result["outcomes"][0]
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
    shutil.rmtree(
        world.git(
            "rev-parse", "--path-format=absolute", "--git-path", "modules", at=path
        )
    )

    def initialize(*args, **kwargs):
        world.git(
            "-c", "protocol.file.allow=always", "submodule", "update", "--init", at=path
        )
        return True

    monkeypatch.setattr(prune, "confirm", initialize)
    assert cli.gw(["remove", "topic", "--delete", "all"]) != 0
    assert (path / "module" / "README").exists()
    assert world.git("rev-parse", "topic")


@pytest.mark.parametrize("entry", ["gw remove", "gw branch delete"])
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
    if entry == "gw remove":
        world.git("checkout", name, at=path)
    p = run(world, entry, name, "--delete", "all", "--yes")
    assert p.returncode != 0
    assert "protected branch" in p.stderr
    assert world.git("rev-parse", name)
    assert path.exists()


def test_no_query_preserves_single_candidate_selection(world):
    path = world.merged_worktree("topic")
    p = run(world, "gw remove", "--yes")
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
    assert cli.gw(["remove", "topic", "--delete", "stashes", "--yes", "--json"]) == 1
    result = json.loads(capsys.readouterr().out)["outcome"]
    assert result["worktree_removed"] == (step == "ref")
    assert not result["branch_removed"]
    assert world.git("rev-parse", "topic")
    assert world.git("rev-parse", "refs/stash") == sha
    assert path.exists() == (step == "checkout")


def test_nested_current_worktree_is_protected(world):
    path = world.worktree("topic")
    (path / "nested").mkdir()
    tip = world.commit("nested/tracked", at=path)
    world.git("merge", "--no-ff", "-qm", "merge", "topic")
    nested = path / "nested"
    preview = run(world, "gw prune", "--delete", "all", "--json", at=nested)
    assert preview.returncode == 0, preview.stderr
    row = next(
        r for r in json.loads(preview.stdout)["verdicts"] if r["branch"] == "topic"
    )
    assert row["verdict"] == "remove"
    assert not row["eligible"]
    assert "you are standing in it" in row["protections"]
    result = run(world, "gw prune", "--delete", "all", "--yes", "--json", at=nested)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["removed"] == []
    assert (nested / "tracked").read_text() == "x"
    assert world.git("rev-parse", "topic") == tip


@pytest.mark.parametrize("keep_branch", [False, True])
def test_nested_current_removal_requires_terminal(world, keep_branch):
    path = world.worktree("topic")
    (path / "nested").mkdir()
    tip = world.commit("nested/tracked", at=path)
    world.git("merge", "--no-ff", "-qm", "merge", "topic")
    flags = ["--keep-branch"] if keep_branch else []
    refused = run(world, "gw remove", str(path), "--yes", *flags, at=path / "nested")
    assert refused.returncode != 0, refused.stderr
    assert "terminal" in refused.stderr
    assert refused.stdout == ""
    assert (path / "nested" / "tracked").exists()
    assert world.git("rev-parse", "topic") == tip
    code, output = run_on_a_terminal_at(
        world, path / "nested", "gw remove", str(path), "--yes", *flags, answer="y\n"
    )
    assert code == 0, output
    assert str(path) in output
    assert output.rstrip().endswith(str(world.repo))
    assert not path.exists()
    refs = world.git("branch", "--format=%(refname:short)").splitlines()
    assert ("topic" in refs) is keep_branch


@pytest.mark.parametrize(
    "entry", ["gw prune", "gw remove", "keep-branch", "gw branch delete", "foreign"]
)
def test_cleanup_preserves_neighbor_repository(world, entry):
    own = world.merged_worktree("done")
    neighbor = world.sibling("neighbor", "done")
    world.commit(".gitignore", "*.secret\n", at=neighbor)
    world.commit("private-branch", at=neighbor)
    (neighbor / "README").write_text("neighbor 'quoted'; $(data)")
    world.git("stash", "push", "-qm", "neighbor 'quoted'; $(data)", at=neighbor)
    other_stash = world.git("rev-parse", "refs/stash", at=neighbor)
    (neighbor / "private.secret").write_bytes(b"neighbor ignored\n")

    def neighbor_state() -> tuple[str, str, str, bytes, bytes]:
        return (
            world.git("worktree", "list", "--porcelain", at=neighbor),
            world.git("for-each-ref", "--format=%(refname) %(objectname)", at=neighbor),
            world.git("stash", "list", "--format=%H %gs", at=neighbor),
            (neighbor / "private.secret").read_bytes(),
            (neighbor / "private-branch").read_bytes(),
        )

    before = neighbor_state()
    args: tuple[str, ...]
    if entry == "gw branch delete":
        world.git("worktree", "remove", str(own))
        args = ("gw branch delete", "done", "--delete", "all", "--yes")
    elif entry == "gw prune":
        args = ("gw prune", "--delete", "all", "--yes")
    elif entry == "keep-branch":
        args = ("gw remove", str(own), "--keep-branch", "--yes")
    elif entry == "foreign":
        args = ("gw remove", str(neighbor), "--delete", "all", "--yes")
    else:
        args = ("gw remove", str(own), "--delete", "all", "--yes")
    proc = run(world, *args)
    assert (proc.returncode != 0) is (entry == "foreign"), proc.stderr
    assert own.exists() is (entry == "foreign")
    assert neighbor.exists()
    assert neighbor_state() == before
    assert world.git("rev-parse", "refs/stash", at=neighbor) == other_stash
    refs = world.git("branch", "--format=%(refname:short)").splitlines()
    assert ("done" in refs) is (entry in ("keep-branch", "foreign"))


@pytest.mark.parametrize("mode", ["all", "selector", "prune"])
def test_batch_modes_keep_protected_refs(world, mode):
    control = world.merged_worktree("eligible")
    world.git("branch", "master")
    world.git("branch", "trunk")
    world.git("checkout", "-qb", "holding")
    world.git("remote", "add", "origin", "https://example.invalid/repo")
    world.git("update-ref", "refs/remotes/origin/trunk", "trunk")
    world.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
    tips = {name: world.git("rev-parse", name) for name in ("main", "master", "trunk")}
    protected = []
    if mode == "prune":
        for name in tips:
            path = world.root / ("protected-" + name)
            world.git("worktree", "add", "--quiet", str(path), name)
            protected.append(path)
        proc = run(world, "gw prune", "--delete", "all", "--yes")
        assert proc.returncode == 0, proc.stderr
    else:
        world.git("worktree", "remove", str(control))
        if mode == "all":
            proc = run(world, "gw branch delete", "--all", "--delete", "all", "--yes")
            assert proc.returncode == 0, proc.stderr
        else:
            code, output = run_on_a_terminal_at(
                world,
                world.repo,
                "gw branch delete",
                "--delete",
                "all",
                answer="1\ny\n",
            )
            assert code == 0, output
            for name in tips:
                assert not re.search(rf"^\s*\d+\s+\w+\s+{name}\s", output, re.M), output
                assert re.search(rf"^\s*-\s+\w+\s+{name}\s", output, re.M), output
    assert not control.exists()
    assert (
        "eligible" not in world.git("branch", "--format=%(refname:short)").splitlines()
    )
    assert all(path.exists() for path in protected)
    assert {name: world.git("rev-parse", name) for name in tips} == tips
    assert world.repo.exists()


@pytest.mark.parametrize(
    "entry,flag",
    [
        ("gw remove", "--force"),
        ("gw remove", "-f"),
        ("gw remove", "--delete-ignored"),
        ("gw prune", "--delete-ignored"),
    ],
)
def test_retired_removal_flags_refuse_before_preflight(world, network, entry, flag):
    world.commit(".gitignore", "*.secret\n")
    path = world.merged_worktree("topic")
    saved = stash(world, path)
    (path / "private.secret").write_bytes(b"preserve ignored\n")
    tip = world.git("rev-parse", "topic")
    before = world.git("worktree", "list", "--porcelain")
    proc = subprocess_cli(
        world,
        *(["topic"] if entry == "gw remove" else []),
        flag,
        "--yes",
        entry=entry,
        env=network.env(),
    )
    assert proc.returncode == 2, proc.stderr
    assert proc.stdout == ""
    assert not network.calls()
    assert world.git("worktree", "list", "--porcelain") == before
    assert world.git("rev-parse", "topic") == tip
    assert world.git("rev-parse", "refs/stash") == saved
    assert (path / "private.secret").read_bytes() == b"preserve ignored\n"


@pytest.mark.parametrize("entry", ["gw remove", "gw branch delete"])
@pytest.mark.parametrize("pending", [False, True])
def test_stash_repair_uses_preceding_listed_subject(world, network, entry, pending):
    path = world.merged_worktree("topic")
    subject = "older 'quoted'; $(data)"
    older = stash(world, world.repo, subject)
    target = stash(world, path, "target")
    pending_sha = stash(world, path, "pending") if pending else None
    if entry == "gw branch delete":
        world.git("worktree", "remove", str(path))
    marker = world.root / "stash-race.json"
    injection = (
        "    if command[:2] == ['stash', 'drop']:\n"
        "        listing = [real_git, *prefix, 'stash', 'list', '--format=%H %gs']\n"
        "        before = subprocess.check_output(listing, text=True)\n"
        "        subprocess.run([real_git, *args], check=True, capture_output=True)\n"
        "        remaining = subprocess.check_output(listing, text=True)\n"
        "        actual = subprocess.run([real_git, *args], "
        "capture_output=True, text=True)\n"
        "        pending_drop = ''\n"
        f"        if {pending!r}:\n"
        "            pending_drop = subprocess.check_output([real_git, *prefix, "
        "'stash', 'drop', 'stash@{0}'], text=True)\n"
        f"        marker = pathlib.Path({str(marker)!r})\n"
        "        marker.write_text(json.dumps({'before': before, "
        "'remaining': remaining, 'actual': actual.stdout, "
        "'pending_drop': pending_drop}))\n"
        "        sys.stdout.write(actual.stdout)\n"
        "        sys.stderr.write(actual.stderr)\n"
        "        sys.exit(actual.returncode)\n"
    )
    network.intercept(injection)
    proc = subprocess_cli(
        world,
        "topic",
        "--delete",
        "stashes",
        "--yes",
        "--json",
        entry=entry,
        env=network.env(),
    )
    assert proc.returncode == 1, proc.stderr
    assert marker.exists()
    race = json.loads(marker.read_text())
    assert target in race["before"] and older in race["before"]
    expected_remaining = f"{older} On main: {subject}\n"
    if pending_sha:
        expected_remaining = f"{pending_sha} On topic: pending\n" + expected_remaining
        assert f"({pending_sha})" in race["pending_drop"]
    assert race["remaining"] == expected_remaining
    assert f"({older})" in race["actual"]
    assert (
        world.git("stash", "list", "--format=%H %gs") == f"{older} On main: {subject}"
    )
    assert "not dropped" in proc.stderr
    assert f"restored {older}" in proc.stderr
    assert (
        f"git stash store -m {shlex.quote('On topic: target')} {target}" in proc.stderr
    )
    outcome = (
        json.loads(proc.stdout)["outcome"]
        if entry == "gw remove"
        else json.loads(proc.stdout)["outcomes"][0]
    )
    assert outcome["branch_removed"]
    assert outcome["dropped"] == []
    assert outcome["retained"] == []
    absent = sorted([target, *([pending_sha] if pending_sha else [])])
    assert outcome["skipped"] == absent
    for sha in absent:
        assert f"stash {sha}: already absent" in proc.stderr
        assert f"stash {sha}: retained" not in proc.stderr
    assert not any(
        call[-4:] == ["log", "-1", "--format=%s", older] for call in network.calls()
    )


@pytest.mark.parametrize("entry", ["gw prune", "gw branch delete"])
def test_later_batch_target_is_rechecked(world, network, entry):
    first = world.merged_worktree("a-first")
    second = world.merged_worktree("b-second")
    second_tip = world.git("rev-parse", "b-second")
    saved = stash(world, second, "second retained")
    if entry == "gw branch delete":
        world.git("worktree", "remove", str(first))
        world.git("worktree", "remove", str(second))
    marker = world.root / "batch-race.json"
    injection = (
        "    if command[:4] == ['update-ref', '--no-deref', '-d', "
        "'refs/heads/a-first']:\n"
        "        actual = subprocess.run([real_git, *args], "
        "capture_output=True, text=True)\n"
        "        if actual.returncode == 0:\n"
        f"            repo = {str(world.repo)!r}\n"
        "            subprocess.run([real_git, '-C', repo, 'update-ref', "
        "'refs/heads/b-second', 'main'], check=True)\n"
        f"            marker = pathlib.Path({str(marker)!r})\n"
        "            marker.write_text(json.dumps({'first_deleted': True}))\n"
        "        sys.stdout.write(actual.stdout)\n"
        "        sys.stderr.write(actual.stderr)\n"
        "        sys.exit(actual.returncode)\n"
    )
    network.intercept(injection)
    proc = subprocess_cli(
        world,
        *(["a-first", "b-second"] if entry == "gw branch delete" else []),
        "--delete",
        "all",
        "--yes",
        "--json",
        entry=entry,
        env=network.env(),
    )
    assert proc.returncode == 1, proc.stderr
    assert marker.exists()
    assert json.loads(marker.read_text()) == {"first_deleted": True}
    assert not first.exists()
    assert world.git("rev-parse", "b-second") == world.git("rev-parse", "main")
    assert world.git("rev-parse", "b-second") != second_tip
    assert world.git("stash", "list", "--format=%H") == saved
    assert (
        "a-first" not in world.git("branch", "--format=%(refname:short)").splitlines()
    )
    data = json.loads(proc.stdout)
    assert data["removed"] == ["a-first"]
    assert "b-second" in proc.stderr
    if entry == "gw prune":
        assert second.exists()
        assert (second / "merged-b-second").read_text() == "x"
        assert data["outcomes"][0]["worktree_removed"]
        assert data["outcomes"][0]["branch_removed"]
        assert not data["outcomes"][1]["worktree_removed"]
        assert not data["outcomes"][1]["branch_removed"]
    else:
        assert data["failed"][0]["branch"] == "b-second"
        assert data["outcomes"][0]["branch_removed"]
    assert not any(
        call[-5:-1] == ["update-ref", "--no-deref", "-d", "refs/heads/b-second"]
        for call in network.calls()
    )


@pytest.mark.parametrize("entry", ["gw remove", "gw prune"])
def test_removed_ignored_entry_invalidates_approval(world, entry):
    world.commit(".gitignore", "*.secret\n")
    path = world.merged_worktree("topic")
    (path / "old.secret").write_text("old")
    tip = world.git("rev-parse", "topic")
    parent, child = pty.openpty()
    proc = subprocess.Popen(
        argv(
            entry,
            *(["topic"] if entry == "gw remove" else []),
            "--delete",
            "all",
            *(["--json"] if entry == "gw remove" else []),
        ),
        cwd=world.repo,
        env=env_for(world),
        stdin=child,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    os.close(child)
    assert proc.stderr is not None
    disclosure = b""
    try:
        deadline = time.monotonic() + 30
        while b"[y/N]" not in disclosure and time.monotonic() < deadline:
            ready, _, _ = select.select([proc.stderr], [], [], 1)
            if ready:
                chunk = os.read(proc.stderr.fileno(), 65536)
                if not chunk:
                    break
                disclosure += chunk
        assert b"[y/N]" in disclosure, disclosure.decode()
        assert b"old.secret" in disclosure
        (path / "old.secret").unlink()
        os.write(parent, b"y\n")
        stdout, stderr = proc.communicate(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        os.close(parent)
    assert proc.returncode == 1, (disclosure + stderr).decode()
    assert "changed after assessment" in (disclosure + stderr).decode()
    if entry == "gw remove":
        data = json.loads(stdout)
        assert not data["removed"]
    else:
        assert b"VERDICT" in stdout and b"ELIGIBLE" in stdout
        assert str(path).encode() in stdout
    assert path.exists()
    assert (path / "merged-topic").read_text() == "x"
    assert not (path / "old.secret").exists()
    assert world.git("rev-parse", "topic") == tip
