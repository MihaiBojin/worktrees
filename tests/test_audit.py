"""The audit log: what a run appends, and what it never does to the file."""

from __future__ import annotations

import json
import os
import pty
import stat
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from conftest import argv, env_for
from test_fast_forward import advance, with_work

from worktrees import audit, cli, forge, prune

COMMON = {"v", "ts", "run", "seq", "event", "user", "host", "pid", "version", "cwd"}


def gw(world, *args: str, at: Path | None = None, **extra: str):
    """Run `gw` with the log on, in a directory of the test's own."""
    env = {
        **env_for(world),
        "WORKTREES_AUDIT": "",
        "WORKTREES_AUDIT_DIR": str(world.root / "audit"),
        **extra,
    }
    return subprocess.run(
        argv("gw", *args),
        cwd=str(at or world.repo),
        capture_output=True,
        text=True,
        env=env,
    )


def files(world) -> list[Path]:
    return sorted((world.root / "audit").glob("*"))


def events(world) -> list[dict]:
    """Every line of every file, each of which must be one JSON object."""
    rows: list[dict] = []
    for path in files(world):
        text = path.read_text()
        assert text.endswith("\n")
        rows += [json.loads(line) for line in text.splitlines()]
    return rows


def named(rows: list[dict], event: str) -> list[dict]:
    return [r for r in rows if r["event"] == event]


@pytest.fixture
def logging(world, monkeypatch):
    """The log on for a test that calls the CLI in this process."""
    monkeypatch.setenv("WORKTREES_AUDIT", "")
    monkeypatch.setenv("WORKTREES_AUDIT_DIR", str(world.root / "audit"))
    return world


# --------------------------------------------------------------------------
# one run
# --------------------------------------------------------------------------


def test_a_run_is_bracketed_and_every_line_carries_who_where_and_when(world):
    p = gw(world, "list", "--json")
    assert p.returncode == 0, p.stderr
    rows = events(world)

    assert [f.name for f in files(world)] == [
        time.strftime("audit-%Y-%m.jsonl", time.gmtime())
    ]
    assert rows[0]["event"] == "invocation.start"
    assert rows[0]["argv"] == ["gw", "list", "--json"]
    assert rows[0]["records"] == ["forge", "mutating", "reads"]
    assert rows[-1]["event"] == "invocation.end"
    assert rows[-1]["code"] == 0
    assert isinstance(rows[-1]["ms"], int)
    for row in rows:
        assert set(row) >= COMMON
        assert row["v"] == 1
        assert row["user"] and row["host"]
        assert row["cwd"] == str(world.repo)
    assert len({r["run"] for r in rows}) == 1
    assert [r["seq"] for r in rows] == list(range(1, len(rows) + 1))


def test_every_git_process_is_recorded_with_its_exit_code_and_time(world):
    assert gw(world, "list", "--json").returncode == 0
    ran = named(events(world), "exec")
    assert ran
    for row in ran:
        assert row["tool"] == "git"
        assert row["argv"][0] == "git"
        assert isinstance(row["code"], int)
        assert isinstance(row["ms"], int)
        assert row["mutates"] is False
        # A read that worked keeps what it printed out of the log.
        assert "out" not in row


def test_a_mutating_command_keeps_what_git_printed(world):
    p = gw(world, "add", "topic")
    assert p.returncode == 0, p.stderr
    [added] = [r for r in named(events(world), "exec") if r["mutates"]]
    assert added["argv"][1:3] == ["worktree", "add"]
    assert added["ok"] is True
    assert "topic" in added["err"]


def test_a_usage_error_and_a_refusal_are_recorded_with_how_they_ended(world):
    assert gw(world, "nonsense").returncode == 2
    assert gw(world, "prune", "--quiet", "--verbose").returncode == 2
    assert gw(world, "remove", "--bogus-flag").returncode == 2
    ends = named(events(world), "invocation.end")
    assert [r["code"] for r in ends] == [2, 2, 2]
    assert "cannot be combined" in ends[1]["error"]
    assert "\x1b" not in ends[1]["error"]


def test_help_and_completion_record_nothing(world):
    for words in (
        ["--help"],
        [],
        ["--explain"],
        ["--complete"],
        ["list", "--complete"],
    ):
        assert gw(world, *words).returncode == 0
    assert files(world) == []


# --------------------------------------------------------------------------
# append only
# --------------------------------------------------------------------------


def test_a_run_appends_and_leaves_every_earlier_byte_as_it_was(world):
    assert gw(world, "list", "--json").returncode == 0
    [path] = files(world)
    first = path.read_bytes()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700

    assert gw(world, "version").returncode == 0
    assert files(world) == [path]
    second = path.read_bytes()
    assert second.startswith(first)
    assert len(second) > len(first)
    assert len({r["run"] for r in events(world)}) == 2


def test_runs_side_by_side_interleave_whole_lines(world):
    with ThreadPoolExecutor(8) as pool:
        done = list(pool.map(lambda _: gw(world, "list", "--json"), range(8)))
    assert all(p.returncode == 0 for p in done)
    rows = events(world)
    assert len(named(rows, "invocation.start")) == 8
    assert len(named(rows, "invocation.end")) == 8
    by_run: dict[str, list[int]] = {}
    for row in rows:
        by_run.setdefault(row["run"], []).append(row["seq"])
    assert all(seqs == list(range(1, len(seqs) + 1)) for seqs in by_run.values())


# --------------------------------------------------------------------------
# what is recorded
# --------------------------------------------------------------------------


def test_reads_off_keeps_the_commands_that_change_something(world):
    p = gw(world, "add", "topic", WORKTREES_AUDIT="reads=off")
    assert p.returncode == 0, p.stderr
    rows = events(world)
    assert rows[0]["records"] == ["forge", "mutating"]
    ran = named(rows, "exec")
    assert ran and all(r["mutates"] for r in ran)


def test_mutating_off_keeps_the_reads(world):
    p = gw(world, "add", "topic", WORKTREES_AUDIT="mutating=off,forge=off")
    assert p.returncode == 0, p.stderr
    ran = named(events(world), "exec")
    assert ran and not any(r["mutates"] for r in ran)


def test_off_writes_no_file(world):
    assert gw(world, "add", "topic", WORKTREES_AUDIT="off").returncode == 0
    assert files(world) == []


@pytest.mark.parametrize("value", ["reads", "reads=maybe", "writes=on", "off,reads=on"])
def test_a_value_it_does_not_take_is_a_usage_error(world, value):
    p = gw(world, "list", "--json", WORKTREES_AUDIT=value)
    assert p.returncode == 2
    assert "WORKTREES_AUDIT takes" in p.stderr
    assert p.stdout == ""
    assert files(world) == []


def test_settings_default_to_everything():
    everything = frozenset(audit.CATEGORIES)
    assert audit.settings("") == everything
    assert audit.settings(" ALL ") == everything
    assert audit.settings("off") is None
    assert audit.settings("reads=off, forge=on") == {"mutating", "forge"}
    assert audit.settings("reads=off,reads=on") == everything


def test_the_state_directory_follows_xdg(monkeypatch, tmp_path):
    monkeypatch.delenv("WORKTREES_AUDIT_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert audit.directory() == tmp_path / "state" / "worktrees"
    # A relative path there is invalid by the specification, and ignored.
    monkeypatch.setenv("XDG_STATE_HOME", "state")
    assert audit.directory() == tmp_path / ".local" / "state" / "worktrees"
    monkeypatch.setenv("WORKTREES_AUDIT_DIR", str(tmp_path / "mine"))
    assert audit.directory() == tmp_path / "mine"


# --------------------------------------------------------------------------
# a log that cannot be written
# --------------------------------------------------------------------------


def test_an_unwritable_log_warns_once_and_the_command_goes_on(world):
    blocker = world.root / "a-file"
    blocker.write_text("")
    p = gw(world, "prune", "--json", "--quiet", WORKTREES_AUDIT_DIR=str(blocker / "x"))
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["verdicts"]
    assert p.stderr.count("audit log not written") == 1
    assert "WORKTREES_AUDIT=off" in p.stderr
    assert blocker.read_text() == ""


# --------------------------------------------------------------------------
# credentials
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "kept"),
    [
        ("https://user:token@example.com/o/r.git", "https://example.com/o/r.git"),
        ("https://token@example.com/o/r", "https://example.com/o/r"),
        ("ssh://git@example.com:22/o/r", "ssh://example.com:22/o/r"),
        ("fatal: 'https://u:p@h/x' not found", "fatal: 'https://h/x' not found"),
        ("git@example.com:o/r.git", "git@example.com:o/r.git"),
        ("https://example.com/a@b", "https://example.com/a@b"),
        ("refs/heads/main", "refs/heads/main"),
    ],
)
def test_scrub_takes_the_credential_out_of_a_url(given, kept):
    assert audit.scrub(given) == kept


def test_a_credential_in_a_remote_url_never_reaches_the_log(world):
    world.git("remote", "add", "origin", "https://bob:s3cret@example.invalid/o/r.git")
    world.merged_worktree("topic")
    p = gw(world, "prune", "--yes")
    assert p.returncode == 0, p.stderr
    [path] = files(world)
    assert "s3cret" not in path.read_text()
    [outcome] = named(events(world), "removal.outcome")
    assert outcome["repository"]["remote"] == "origin"
    assert outcome["repository"]["url"] == "https://example.invalid/o/r.git"
    assert outcome["repository"]["commonDir"] == str(world.repo / ".git")


# --------------------------------------------------------------------------
# removals
# --------------------------------------------------------------------------


def test_a_removal_is_proposed_then_completed_and_its_undo_restores(world):
    path = world.merged_worktree("topic")
    sha = world.git("rev-parse", "refs/heads/topic")
    p = gw(world, "prune", "--yes")
    assert p.returncode == 0, p.stderr
    rows = events(world)
    [proposed] = named(rows, "removal.proposed")
    [outcome] = named(rows, "removal.outcome")
    assert proposed["seq"] < outcome["seq"]

    for row in (proposed, outcome):
        assert (row["branch"], row["sha"], row["path"]) == ("topic", sha, str(path))
        assert row["reason"] == "merged"
        assert row["evidence"]
        assert row["head"]["branch"] == "main"
        assert row["undo"] == [f"git branch topic {sha}"]
        assert "ignored and untracked files do not come back" in row["recovery"]
    assert proposed["deletes"]["worktree"] == str(path)
    assert proposed["deletes"]["branch"] == "topic"
    assert outcome["result"] == "completed"
    assert (outcome["worktreeRemoved"], outcome["branchRemoved"]) == (True, True)

    # The mutating commands the outcome summarises are there too, in order.
    changed = [r["argv"] for r in named(rows, "exec") if r["mutates"]]
    assert [a for a in changed if "remove" in a]
    assert [a for a in changed if "update-ref" in a and sha in a]

    # Reproduce the loss, then assert the recorded line is what undoes it.
    assert "topic" not in world.git("branch", "--format=%(refname:short)").split()
    subprocess.run(outcome["undo"][0], shell=True, check=True, cwd=world.repo)
    assert world.git("rev-parse", "refs/heads/topic") == sha


def test_a_declined_removal_is_proposed_and_has_no_outcome(world):
    world.merged_worktree("topic")
    # A real terminal on stdin, so the question is reached and answered no.
    parent, child = pty.openpty()
    proc = subprocess.Popen(
        argv("gw", "prune"),
        cwd=str(world.repo),
        stdin=child,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env={
            **env_for(world),
            "WORKTREES_AUDIT": "",
            "WORKTREES_AUDIT_DIR": str(world.root / "audit"),
        },
    )
    os.close(child)
    os.write(parent, b"n\n")
    out, _ = proc.communicate(timeout=60)
    os.close(parent)
    assert proc.returncode == 0, out
    rows = events(world)
    assert len(named(rows, "removal.proposed")) == 1
    assert named(rows, "removal.outcome") == []
    assert world.git("rev-parse", "refs/heads/topic")


def test_an_ignored_path_and_a_stash_are_recorded_with_what_restores_them(world):
    path = world.merged_worktree("topic")
    (world.repo / ".gitignore").write_text("build/\n")
    world.git("add", ".gitignore")
    world.git("commit", "--quiet", "-m", "ignore")
    world.git("merge", "--quiet", "main", at=path)
    world.git("merge", "--quiet", "--ff-only", "topic")
    (path / "build").mkdir()
    (path / "build" / "out").write_text("x")
    (path / "README").write_text("pending")
    world.git("stash", "push", "-qm", "it's pending", at=path)
    stashed = world.git("rev-parse", "refs/stash")

    p = gw(world, "prune", "--delete", "all", "--yes")
    assert p.returncode == 0, p.stderr
    [outcome] = named(events(world), "removal.outcome")
    assert outcome["ignoredDeleted"] == ["build/"]
    assert outcome["stashesDropped"] == [stashed]
    restore = next(line for line in outcome["undo"] if "stash store" in line)
    assert not world.git("stash", "list")
    subprocess.run(restore, shell=True, check=True, cwd=world.repo)
    assert world.git("rev-parse", "refs/stash") == stashed
    assert "it's pending" in world.git("stash", "list")


@pytest.mark.parametrize("step", ["checkout", "ref"])
def test_a_partial_failure_records_each_step_on_its_own(
    logging, monkeypatch, capsys, step
):
    world = logging
    path = world.merged_worktree("topic")
    sha = world.git("rev-parse", "refs/heads/topic")
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

    assert cli.gw(["remove", "topic", "--yes", "--json"]) == 1
    capsys.readouterr()
    rows = events(world)
    [outcome] = named(rows, "removal.outcome")
    assert outcome["worktreeRemoved"] == (step == "ref")
    assert outcome["branchRemoved"] is False
    assert outcome["result"] == ("partial" if step == "ref" else "refused")
    assert outcome["failed"]
    assert outcome["sha"] == sha
    # The branch stayed, so nothing recorded claims to restore it.
    assert outcome["undo"] == []
    failed = [r for r in named(rows, "exec") if not r["ok"]]
    assert failed and failed[-1]["err"]
    assert rows[-1]["event"] == "invocation.end"
    assert rows[-1]["code"] == 1
    assert not audit.active()


def test_a_loose_branch_deletion_is_recorded(world):
    sha = world.merged_branch("topic")
    p = gw(world, "branch", "prune", "--all", "--yes")
    assert p.returncode == 0, p.stderr
    rows = events(world)
    [proposed] = named(rows, "removal.proposed")
    [outcome] = named(rows, "removal.outcome")
    assert proposed["path"] == outcome["path"] == ""
    assert (outcome["branch"], outcome["sha"]) == ("topic", sha)
    assert outcome["result"] == "completed"
    assert outcome["undo"] == [f"git branch topic {sha}"]


# --------------------------------------------------------------------------
# fast-forward and the forge
# --------------------------------------------------------------------------


def test_a_rebase_records_where_the_branch_stood(world):
    was = with_work(world)
    advance(world)
    p = gw(world, "fast-forward", "--json")
    assert p.returncode == 0, p.stderr
    rows = events(world)
    [moved] = named(rows, "fast-forward.outcome")
    assert moved["result"] == "rebased"
    assert moved["previous_sha"] == was[:7]
    assert moved["previous_full_sha"] == was
    assert moved["full_sha"] == world.git("rev-parse", "HEAD")
    assert moved["undo"] == [f"git branch rescue-{was[:7]} {was}"]
    assert [r for r in named(rows, "exec") if r["mutates"] and "rebase" in r["argv"]]


def test_a_branch_already_level_records_nothing_to_undo(world):
    world.git("switch", "--quiet", "--create", "fix-parser")
    assert gw(world, "fast-forward", "--json").returncode == 0
    [moved] = named(events(world), "fast-forward.outcome")
    assert (moved["result"], moved["undo"]) == ("current", [])


def test_a_forge_call_is_recorded_and_a_write_is_marked(logging, monkeypatch):
    world = logging
    tools = world.root / "tools"
    tools.mkdir()
    gh = tools / "gh"
    gh.write_text('#!/bin/sh\necho "{}"\necho "HTTP 409" >&2\nexit 1\n')
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tools}{os.pathsep}{os.environ['PATH']}")

    audit.begin("gw", ["merge", "7"])
    try:
        forge._run(["gh", "api", "repos/o/r/pulls/7", "--method", "GET"])
        forge._run(["gh", "api", "repos/o/r/pulls/7/merge", "--method", "PUT"])
        forge._run(["no-such-forge-tool", "auth", "status"])
    finally:
        audit.end(1)

    read, write, absent = named(events(world), "exec")
    assert (read["tool"], read["mutates"], read["code"]) == ("gh", False, 1)
    assert read["ok"] is False
    assert read["err"] == "HTTP 409"
    assert write["mutates"] is True
    assert write["argv"][-1] == "PUT"
    # It never started, so there is no exit code to record.
    assert absent["code"] is None
    assert absent["ok"] is False
    assert absent["err"]


def test_forge_off_drops_forge_calls_only(logging, monkeypatch):
    world = logging
    monkeypatch.setenv("WORKTREES_AUDIT", "forge=off")
    audit.begin("gw", ["sync"])
    try:
        forge._run(["no-such-forge-tool", "auth", "status"])
    finally:
        audit.end(0)
    rows = events(world)
    assert [r["event"] for r in rows] == ["invocation.start", "invocation.end"]


def test_nothing_is_recorded_outside_a_run(logging):
    world = logging
    assert not audit.active()
    audit.record("stray", detail=1)
    prune.stash_list(repo=world.repo)
    assert files(world) == []
