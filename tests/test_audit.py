"""The audit log: what a run appends, and what it never does to the file."""

from __future__ import annotations

import json
import os
import pty
import resource
import signal
import stat
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import audit_schema
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


def lines(world) -> list[str]:
    return [
        line for path in files(world) for line in path.read_text().split("\n") if line
    ]


def parsed(world) -> list[dict]:
    """Every line that is an event, as a reader that skips a fragment has it."""
    rows = []
    for line in lines(world):
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return [audit_schema.check(row) for row in rows]


def events(world) -> list[dict]:
    """Every line of every file, each of which must be one JSON object."""
    rows: list[dict] = []
    for path in files(world):
        text = path.read_text()
        assert text.endswith("\n")
        rows += [audit_schema.check(json.loads(line)) for line in text.splitlines()]
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
    assert [r["failure"] for r in ends] == ["usage", "usage", "usage"]
    assert "is not a command" in ends[0]["error"]
    # argparse printed that one itself, so there is a kind and no message.
    assert "error" not in ends[2]
    assert "cannot be combined" in ends[1]["error"]
    assert "\x1b" not in ends[1]["error"]


@pytest.mark.parametrize(
    "words",
    [
        ["--help"],
        [],
        ["--explain"],
        ["--complete"],
        ["list", "--complete"],
        ["list", "--help"],
        ["list", "-h"],
        ["prune", "--explain"],
        ["prune", "--yes", "--explain"],
        ["remove", "topic", "--help"],
        ["branch", "prune", "--help"],
        ["fast-forward", "--explain"],
        # argparse takes an unambiguous prefix of a long option.
        ["prune", "--hel"],
        ["prune", "--expl"],
    ],
)
@pytest.mark.parametrize("setting", ["", "nonsense"])
def test_help_explain_and_completion_record_nothing(world, words, setting):
    """Whatever `WORKTREES_AUDIT` holds: help stays usable under a bad value."""
    p = gw(world, *words, WORKTREES_AUDIT=setting)
    assert p.returncode == 0, p.stderr
    assert "WORKTREES_AUDIT takes" not in p.stderr
    assert files(world) == []


def test_a_word_after_the_separator_is_a_value_and_the_run_is_recorded(world):
    gw(world, "list", "--json", "--", "--help")
    rows = events(world)
    assert rows[0]["argv"] == ["gw", "list", "--json", "--", "--help"]
    assert rows[-1]["event"] == "invocation.end"


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


def limited(size: int):
    """A file size limit for a child, which is told of it by a short write
    and then an error, not by a signal: the way a full disk is met."""

    def apply() -> None:
        signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
        resource.setrlimit(resource.RLIMIT_FSIZE, (size, size))

    return apply


def test_a_torn_line_loses_itself_and_nothing_after_it(world):
    env = {
        **env_for(world),
        "WORKTREES_AUDIT": "",
        "WORKTREES_AUDIT_DIR": str(world.root / "audit"),
    }
    torn = subprocess.run(
        argv("gw", "version"),
        cwd=str(world.repo),
        capture_output=True,
        text=True,
        env=env,
        preexec_fn=limited(120),
    )
    assert torn.returncode == 0, torn.stderr
    assert torn.stderr.count("audit log not written") == 1
    [path] = files(world)
    fragment = path.read_bytes()
    # Reproduce the loss: a fragment the limit left no room to close.
    assert len(fragment) == 120
    assert not fragment.endswith(b"\n")

    assert gw(world, "version").returncode == 0
    after = path.read_bytes()
    assert after.startswith(fragment + b"\n")
    first, *rest = lines(world)
    assert first == fragment.decode()
    with pytest.raises(json.JSONDecodeError):
        json.loads(first)
    rows = [json.loads(line) for line in rest]
    assert rows[0]["event"] == "invocation.start"
    assert rows[0]["torn"] is True
    assert all("torn" not in row for row in rows[1:])
    assert rows[-1]["event"] == "invocation.end"

    # A third run finds a file that ends as it should and marks nothing.
    assert gw(world, "version").returncode == 0
    later = [r for r in parsed(world) if r["run"] != rows[0]["run"]]
    assert later and all("torn" not in row for row in later)


def test_runs_side_by_side_all_survive_a_fragment_left_before_them(world):
    (world.root / "audit").mkdir()
    name = time.strftime("audit-%Y-%m.jsonl", time.gmtime())
    (world.root / "audit" / name).write_bytes(b'{"v":1,"event":"invocation.st')
    with ThreadPoolExecutor(8) as pool:
        done = list(pool.map(lambda _: gw(world, "list", "--json"), range(8)))
    assert all(p.returncode == 0 for p in done)

    bad = []
    for line in lines(world):
        try:
            json.loads(line)
        except json.JSONDecodeError:
            bad.append(line)
    assert bad == ['{"v":1,"event":"invocation.st']
    rows = parsed(world)
    assert len(named(rows, "invocation.start")) == 8
    assert len(named(rows, "invocation.end")) == 8
    assert any(r.get("torn") for r in rows)
    by_run: dict[str, list[int]] = {}
    for row in rows:
        by_run.setdefault(row["run"], []).append(row["seq"])
    assert all(seqs == list(range(1, len(seqs) + 1)) for seqs in by_run.values())


def test_a_short_write_is_closed_and_its_rest_is_never_written(
    logging, monkeypatch, capsys
):
    world = logging
    real, calls = os.write, []

    def short(fd, data):
        calls.append(bytes(data))
        if len(calls) == 2:
            return real(fd, bytes(data)[:10])
        return real(fd, data)

    monkeypatch.setattr(os, "write", short)
    audit.begin("gw", ["version"])
    try:
        audit.record("second", detail="x" * 50)
        assert not audit.active()
        audit.record("third")
    finally:
        monkeypatch.setattr(os, "write", real)
        audit.end(0)
    assert "audit log not written" in capsys.readouterr().err
    # The start, the torn line, and the newline that closes it. Nothing
    # resumes the fragment, and nothing is written once the log has failed.
    assert len(calls) == 3
    assert calls[2] == b"\n"
    [path] = files(world)
    raw = path.read_bytes()
    assert raw.endswith(calls[1][:10] + b"\n")
    assert [r["event"] for r in parsed(world)] == ["invocation.start"]

    audit.begin("gw", ["version"])
    audit.end(0)
    after = parsed(world)[1:]
    assert [r["event"] for r in after] == ["invocation.start", "invocation.end"]
    # The fragment was closed where it tore, so nothing has to close it now.
    assert all("torn" not in row for row in after)
    assert b"\n\n" not in path.read_bytes()


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
    # Which step, apart from what it said.
    assert outcome["failedStage"] == ("branch" if step == "ref" else "worktree")
    assert rows[-1]["failure"] == "result"
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


# --------------------------------------------------------------------------
# every string in an event is scrubbed, and recovery is data
# --------------------------------------------------------------------------


def test_clean_scrubs_every_string_however_deep():
    secret = "https://bob:hunter2@example.invalid/r"
    given = {
        "undo": [f"git stash store -m 'see {secret}' abc"],
        "restore": [{"argv": ["git", "-m", secret], "subject": secret}],
        "nested": {"deeper": ({secret}, 3, None, True)},
        secret: "as a key",
    }
    kept = json.dumps(audit.clean(given))
    assert "hunter2" not in kept
    assert "https://example.invalid/r" in kept
    assert json.loads(kept)["nested"]["deeper"][1:] == [3, None, True]


def test_a_credential_in_a_stash_subject_never_reaches_the_log(world):
    path = world.merged_worktree("topic")
    (path / "README").write_text("pending")
    subject = "check https://bob:review-test-secret@example.invalid/repo"
    world.git("stash", "push", "-qm", subject, at=path)
    assert "review-test-secret" in world.git("stash", "list")

    p = gw(world, "prune", "--delete", "all", "--yes")
    assert p.returncode == 0, p.stderr
    [log] = files(world)
    assert "review-test-secret" not in log.read_text()
    rows = events(world)
    for row in (*named(rows, "removal.proposed"), *named(rows, "removal.outcome")):
        [stashed] = [r for r in row["restore"] if r["kind"] == "stash"]
        assert stashed["subject"] == "On topic: check https://example.invalid/repo"
        assert any("https://example.invalid/repo" in line for line in row["undo"])


def test_a_kept_branch_keeps_its_stashes_out_of_what_is_proposed(world):
    path = world.merged_worktree("topic")
    (path / "README").write_text("pending")
    world.git("stash", "push", "-qm", "on topic", at=path)
    (world.repo / "README").write_text("pending on main")
    world.git("stash", "push", "-qm", "on main")
    before = world.git("stash", "list").splitlines()
    assert len(before) == 2

    p = gw(world, "remove", "topic", "--keep-branch", "--yes")
    assert p.returncode == 0, p.stderr
    rows = events(world)
    [proposed] = named(rows, "removal.proposed")
    [outcome] = named(rows, "removal.outcome")
    assert proposed["deletes"]["stashes"] == []
    for row in (proposed, outcome):
        assert row["restore"] == []
        assert row["undo"] == []
    assert outcome["worktreeRemoved"] is True
    assert outcome["branchRemoved"] is False
    assert world.git("stash", "list").splitlines() == before


def test_recovery_is_fields_a_program_reads_without_a_shell(world):
    path = world.merged_worktree("topic")
    tip = world.git("rev-parse", "refs/heads/topic")
    (path / "README").write_text("pending")
    said = "it's pending: Unicode caf\u00e9 and  two spaces"
    world.git("stash", "push", "-qm", said, at=path)
    stashed = world.git("rev-parse", "refs/stash")
    # As git keeps it: a reflog message has each run of spaces made one.
    subject = world.git("log", "-g", "-1", "--format=%gs", "refs/stash")
    assert subject == "On topic: it's pending: Unicode caf\u00e9 and two spaces"

    p = gw(world, "prune", "--delete", "all", "--yes")
    assert p.returncode == 0, p.stderr
    [outcome] = named(events(world), "removal.outcome")
    branch, stash = outcome["restore"]
    assert branch == {
        "kind": "branch",
        "sha": tip,
        "branch": "topic",
        "argv": ["git", "branch", "topic", tip],
        "command": f"git branch topic {tip}",
    }
    assert (stash["kind"], stash["sha"]) == ("stash", stashed)
    assert stash["subject"] == subject
    assert stash["argv"] == ["git", "stash", "store", "-m", stash["subject"], stashed]
    # The line a person pastes is the same actions, in the same order.
    assert outcome["undo"] == [branch["command"], stash["command"]]

    # Run each as the argv it is: no shell, and nothing parsed out of text.
    assert not world.git("stash", "list")
    for action in outcome["restore"]:
        subprocess.run(action["argv"], check=True, cwd=world.repo)
    assert world.git("rev-parse", "refs/heads/topic") == tip
    assert world.git("rev-parse", "refs/stash") == stashed
    assert world.git("log", "-g", "-1", "--format=%gs", "refs/stash") == subject


@pytest.mark.parametrize(
    "subject",
    [
        "plain",
        'it\'s "quoted" $(and) `more`; rm -rf ~',
        "caf\u00e9 \u2603 \U0001f600",
        "line one\nline two\n",
        "tab\there  and   spaces ",
    ],
)
def test_a_restore_action_round_trips_through_the_log(logging, subject):
    world = logging
    tip, lost, held = "a" * 40, "b" * 40, "c" * 40
    head = prune.HeadState(
        "refs/heads/main", "refs/heads/main", tip, "main", frozenset(), ()
    )
    snapshot = prune.Snapshot(
        head=head,
        branch=subject,
        sha=tip,
        stashes=((held, subject),),
        reflog=((lost, subject),),
    )
    built = prune.restores(snapshot, True, [held])
    audit.begin("gw", ["prune"])
    try:
        audit.record("removal.outcome", restore=built)
    finally:
        audit.end(0)

    # A bare event, to carry the actions alone; they are held to the schema.
    [row] = [
        r for r in map(json.loads, lines(world)) if r["event"] == "removal.outcome"
    ]
    audit_schema._fits(row["restore"], [audit_schema.ACTION], "restore")
    assert row["restore"] == built
    reflog, branch, stash = row["restore"]
    assert (reflog["kind"], reflog["sha"], reflog["subject"]) == (
        "reflog-commit",
        lost,
        subject,
    )
    assert reflog["argv"] == ["git", "branch", f"rescue-{lost[:7]}", lost]
    assert (branch["branch"], branch["argv"][2]) == (subject, subject)
    assert (stash["subject"], stash["argv"][4]) == (subject, subject)
    # The pasted line says the same thing to a shell, for each of them.
    for action in row["restore"]:
        echoed = subprocess.run(
            ["sh", "-c", f"printf '%s\\0' {action['command']}"],
            capture_output=True,
            check=True,
        ).stdout
        assert echoed.decode().split("\0")[:-1] == action["argv"]


def test_a_rebase_records_its_previous_tip_as_a_restore_action(world):
    was = with_work(world)
    advance(world)
    assert gw(world, "fast-forward", "--json").returncode == 0
    [moved] = named(events(world), "fast-forward.outcome")
    [action] = moved["restore"]
    assert action == {
        "kind": "previous-tip",
        "sha": was,
        "of": "fix-parser",
        "branch": f"rescue-{was[:7]}",
        "argv": ["git", "branch", f"rescue-{was[:7]}", was],
        "command": f"git branch rescue-{was[:7]} {was}",
    }
    subprocess.run(action["argv"], check=True, cwd=world.repo)
    assert world.git("rev-parse", f"rescue-{was[:7]}") == was


# --------------------------------------------------------------------------
# what a reader is promised about each field
# --------------------------------------------------------------------------


def test_how_a_run_ended_is_a_kind_apart_from_its_message(world):
    world.merged_branch("gone")
    assert gw(world, "version").returncode == 0
    # Not a terminal and nothing named: a refusal, at exit 3.
    assert gw(world, "branch", "prune", "--yes").returncode == 3
    # A base that is no commit: git's failure, at exit 1.
    assert gw(world, "add", "topic", "no-such-base").returncode != 0
    ok, refused, broken = named(events(world), "invocation.end")
    assert (ok["code"], ok["failure"]) == (0, None)
    assert "error" not in ok
    assert (refused["code"], refused["failure"]) == (3, "refused")
    assert refused["error"] and refused["errorDropped"] == 0
    assert broken["failure"] in ("git", "refused")
    assert broken["error"]


def test_a_recheck_that_refuses_names_its_stage(logging, monkeypatch, capsys):
    world = logging
    world.merged_worktree("topic")
    original = prune.check_head

    def moved(*args, **kwargs):
        world.commit("late")
        return original(*args, **kwargs)

    monkeypatch.setattr(prune, "check_head", moved)
    assert cli.gw(["remove", "topic", "--yes", "--json"]) == 1
    capsys.readouterr()
    [outcome] = named(events(world), "removal.outcome")
    assert (outcome["result"], outcome["failedStage"]) == ("refused", "recheck")
    assert outcome["restore"] == []


def test_a_completed_removal_has_no_failed_stage(world):
    world.merged_worktree("topic")
    assert gw(world, "prune", "--yes").returncode == 0
    [outcome] = named(events(world), "removal.outcome")
    assert (outcome["failed"], outcome["failedStage"]) == ([], None)


def test_what_was_cut_from_an_output_is_a_count_beside_it(logging):
    world = logging
    audit.begin("gw", ["sync"])
    try:
        for err in ("x" * 4000, "y" * 4001, "z" * 9000):
            audit.ran(
                "git",
                ["git", "fetch"],
                1,
                time.monotonic(),
                category="mutating",
                mutates=True,
                ok=False,
                err=err,
            )
        audit.failed("m" * 5000, "git")
    finally:
        audit.end(1)
    rows = events(world)
    whole, one, many = named(rows, "exec")
    assert (len(whole["err"]), whole["errDropped"]) == (4000, 0)
    assert (len(one["err"]), one["errDropped"]) == (4000, 1)
    assert (many["err"], many["errDropped"]) == ("z" * 4000, 5000)
    # Nothing printed, nothing recorded: neither field, not an empty one.
    assert "out" not in whole and "outDropped" not in whole
    assert (len(rows[-1]["error"]), rows[-1]["errorDropped"]) == (4000, 1000)


def test_a_value_no_reader_was_promised_is_never_written_as_its_repr(logging, capsys):
    world = logging
    audit.begin("gw", ["version"])
    try:
        audit.record("merge.outcome", pr=object())
        assert "audit event merge.outcome not written" in capsys.readouterr().err
        # One event is lost and the run goes on being recorded.
        assert audit.active()
    finally:
        audit.end(0)
    [log] = files(world)
    assert "object at 0x" not in log.read_text()
    assert [r["event"] for r in events(world)] == ["invocation.start", "invocation.end"]


def test_a_message_is_recorded_without_the_colour_a_terminal_gave_it():
    assert audit.clean({"failed": ["\x1b[1mgit branch\x1b[0m exited 1"]}) == {
        "failed": ["git branch exited 1"]
    }


def test_the_schema_refuses_what_it_does_not_list(world):
    assert gw(world, "version").returncode == 0
    start = events(world)[0]
    for broken in (
        {**start, "extra": 1},
        {k: v for k, v in start.items() if k != "terminal"},
        {**start, "terminal": "yes"},
        {**start, "seq": True},
        {**start, "records": ["everything"]},
        {**start, "event": "invocation.begin"},
    ):
        with pytest.raises(AssertionError):
            audit_schema.check(broken)


def test_the_reference_names_every_field_and_value_a_reader_meets():
    text = (Path(__file__).parents[1] / "docs" / "REFERENCE.md").read_text()
    section = text[text.index("## Audit log") : text.index("## Sync cache")]
    missing = sorted(n for n in audit_schema.names() if f"`{n}`" not in section)
    assert not missing, f"fields the reference does not name: {missing}"
    values = [*audit_schema.EVENTS, *audit.FAILURES, *prune.STAGES, *audit.CATEGORIES]
    values += ["completed", "partial", "refused", *audit_schema.ACTION.shapes]
    unsaid = sorted(v for v in values if f"`{v}`" not in section)
    assert not unsaid, f"values the reference does not name: {unsaid}"
