"""A repository fixture that needs no global config, key, forge or network.

GIT_CONFIG_GLOBAL carries the weight: without it git still reads
$HOME/.gitconfig, and setting HOME alone still leaves $XDG_CONFIG_HOME/git/
config. On a machine with commit.gpgsign=true a fixture inheriting it fails at
the first commit and every assertion after that is meaningless.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

SRC = str(Path(__file__).resolve().parents[1] / "src")


def _path() -> str:
    """The PATH a driven test runs under, with this machine's git first.

    `/usr/bin` came first for a long time, which on macOS is Apple's stub
    rather than the git a person here runs. It re-execs the real binary, so
    every call paid for it twice, and after an Xcode update it answers every
    command with exit 69 until somebody accepts a licence. CI never saw
    either, because on Linux that path is a real git.

    Still a list rather than `os.environ["PATH"]`: a test inherits no more of
    the machine than it needs.
    """
    found = shutil.which("git")
    first = [str(Path(found).parent)] if found else []
    rest = [
        d
        for d in ("/usr/bin", "/bin", "/usr/local/bin", "/opt/homebrew/bin")
        if d not in first
    ]
    return os.pathsep.join([*first, *rest])


PATH = _path()

# Fixed dates make the first commit's sha a constant, so a test may assert on
# a literal one.
ENV = {
    "GIT_AUTHOR_NAME": "T",
    "GIT_AUTHOR_EMAIL": "t@localhost",
    "GIT_AUTHOR_DATE": "@1700000000 +0000",
    "GIT_COMMITTER_NAME": "T",
    "GIT_COMMITTER_EMAIL": "t@localhost",
    "GIT_COMMITTER_DATE": "@1700000000 +0000",
}


class World:
    """One repository, its worktrees, and a git that runs against it."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.repo = root / "repo"
        self.parent = root

    def git(self, *args: str, at: Path | None = None) -> str:
        proc = subprocess.run(
            ["git", "-C", str(at or self.repo), *args],
            capture_output=True,
            text=True,
            check=True,
        )
        return proc.stdout.strip()

    def commit(self, name: str, body: str = "x", at: Path | None = None) -> str:
        (at or self.repo).joinpath(name).write_text(body)
        self.git("add", "--", name, at=at)
        self.git("commit", "--quiet", "-m", name, at=at)
        return self.git("rev-parse", "HEAD", at=at)

    def worktree(self, branch: str, base: str = "main") -> Path:
        """The derived layout: <PARENT>/.worktrees/<branch>/<repo>."""
        path = self.parent / ".worktrees" / branch / self.repo.name
        path.parent.mkdir(parents=True, exist_ok=True)
        self.git("worktree", "add", "--quiet", "-b", branch, str(path), base)
        return path

    def merged_worktree(self, branch: str) -> Path:
        """A clean worktree whose side-branch tip is merged with two parents."""
        path = self.worktree(branch)
        self.commit("merged-" + branch.replace("/", "-"), at=path)
        self.git("merge", "--quiet", "--no-ff", "-m", "merge " + branch, branch)
        return path

    def merged_branch(self, branch: str) -> str:
        """A loose branch with its tip merged through a merge commit."""
        path = self.merged_worktree(branch)
        sha = self.git("rev-parse", f"refs/heads/{branch}")
        self.git("worktree", "remove", str(path))
        return sha

    def sibling(self, name: str, branch: str) -> Path:
        """A second repository beside this one, with a worktree of its own.

        The repository name goes last in the derived layout, so repositories
        side by side share `<PARENT>/.worktrees` without colliding.
        """
        other = self.root / name
        self.git("init", "--quiet", "-b", "main", str(other))
        self.commit("README", at=other)
        path = self.parent / ".worktrees" / branch / name
        path.parent.mkdir(parents=True, exist_ok=True)
        self.git(
            "worktree", "add", "--quiet", "-b", branch, str(path), "main", at=other
        )
        return path


def argv(entry: str, *args: str) -> list[str]:
    """Run `entry` from this checkout's source, without installing it.

    `entry` is a command as typed: `gw prune` calls the `gw` entry point with
    `prune` as its first argument, and `main` is the one `worktrees` installs.
    """
    name, *words = entry.split()
    return [
        sys.executable,
        "-c",
        f"from worktrees.cli import {name}; raise SystemExit({name}())",
        *words,
        *args,
    ]


def env_for(world: World) -> dict[str, str]:
    """What a driven test hands the console script it runs.

    One copy. There were five, and the PATH inside them had to be wrong five
    times before it was noticed once.
    """
    return {
        "PATH": PATH,
        "PYTHONPATH": SRC,
        "HOME": str(world.root),
        "GIT_CONFIG_GLOBAL": str(world.root / "gitconfig"),
        "GIT_CONFIG_NOSYSTEM": "1",
        **ENV,
    }


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    (tmp_path / "gitconfig").write_text("[init]\n\tdefaultBranch = main\n")

    w = World(tmp_path)
    w.repo.mkdir()
    subprocess.run(
        ["git", "init", "--quiet", "-b", "main", str(w.repo)],
        check=True,
        capture_output=True,
    )
    w.commit("README")
    monkeypatch.chdir(w.repo)
    return w


@pytest.fixture(autouse=True)
def fresh_log() -> None:
    """Every test asserts on the git calls its own run issued."""
    from worktrees.git import options

    options.log.clear()
    options.verbose = bool(os.environ.get("WT_VERBOSE"))


END = "\x1eend-of-call\x1e"


class Network:
    """Process wrappers that record calls and answer only network operations."""

    def __init__(self, world: World) -> None:
        self.world = world
        self.bin = world.root / "network-bin"
        self.bin.mkdir()
        self.log = world.root / "network-calls.log"
        self.responses = world.root / "network-responses.json"
        self.responses.write_text("{}")
        real_git = shutil.which("git")
        # git runs on every call a command makes, forty in one gwp, so its
        # wrapper is sh: a Python one costs an interpreter start each time.
        # A record is its argv NUL-terminated, then END, in one write.
        record = f"printf '%s\\0' \"$tool\" \"$@\" '{END}' >> '{self.log}'"
        (self.bin / "git").write_text(
            "#!/bin/sh\n"
            "tool=git\n"
            f"{record}\n"
            f"real='{real_git}'\n"
            'if [ "$1" = -C ]; then dir=$2; shift 2; set -- -C "$dir" "$@"; '
            "sub=$3; next=$4; remote=$5; else sub=$1; next=$2; remote=$3; fi\n"
            'if [ "$sub" = fetch ]; then exit "${WT_FETCH_CODE:-0}"; fi\n'
            'if [ "$sub" = remote ] && [ "$next" = set-head ]; then\n'
            '    if [ -n "$WT_HEAD_CODE" ]; then exit "$WT_HEAD_CODE"; fi\n'
            '    if [ "$1" = -C ]; then set -- -C "$2"; else set --; fi\n'
            '    exec "$real" "$@" symbolic-ref "refs/remotes/$remote/HEAD" '
            '"refs/remotes/$remote/main"\n'
            "fi\n"
            'exec "$real" "$@"\n'
        )
        script = (
            f"#!{sys.executable}\n"
            "import json, os, pathlib, sys\n"
            f"log = pathlib.Path({str(self.log)!r})\n"
            f"responses = pathlib.Path({str(self.responses)!r})\n"
            "tool = pathlib.Path(sys.argv[0]).name\n"
            "args = sys.argv[1:]\n"
            "with log.open('ab') as stream:\n"
            "    stream.write(os.fsencode('\\0'.join([tool, *args, "
            f"{END!r}]) + '\\0'))\n"
            "if os.environ.get('WT_FORGE_CODE'):\n"
            "    sys.exit(int(os.environ['WT_FORGE_CODE']))\n"
            "if args[:2] == ['auth', 'status']:\n"
            "    sys.exit(int(os.environ.get('WT_AUTH_CODE', '0')))\n"
            "if args[:1] == ['api']:\n"
            "    records = json.loads(responses.read_text()).get('$api', {})\n"
            "    answer = records.get(args[1])\n"
            "    if answer is None: sys.exit(1)\n"
            "    print(json.dumps(answer))\n"
            "    sys.exit(0)\n"
            "flag = '--head' if tool == 'gh' else '--source-branch'\n"
            "branch = args[args.index(flag) + 1]\n"
            "answer = json.loads(responses.read_text()).get(branch, [])\n"
            "if isinstance(answer, int):\n"
            "    sys.exit(answer)\n"
            "print(json.dumps(answer))\n"
        )
        for name in ("git", "gh", "glab"):
            path = self.bin / name
            if name != "git":
                path.write_text(script)
            path.chmod(0o755)

    def intercept(self, code: str) -> None:
        """Run `code` inside a Python git wrapper before the usual answers.

        Slower than the sh one, so only a test that races git uses it. The
        code is indented four spaces and sees real_git, prefix, args and
        command.
        """
        real_git = shutil.which("git")
        (self.bin / "git").write_text(
            f"#!{sys.executable}\n"
            "import json, os, pathlib, subprocess, sys\n"
            f"real_git = {real_git!r}\n"
            "args = sys.argv[1:]\n"
            f"with open({str(self.log)!r}, 'ab') as stream:\n"
            "    stream.write(os.fsencode('\\0'.join(['git', *args, "
            f"{END!r}]) + '\\0'))\n"
            "prefix = args[:2] if args[:1] == ['-C'] else []\n"
            "command = args[len(prefix):]\n"
            "if True:\n"
            f"{code}"
            "if command[:1] == ['fetch']:\n"
            "    sys.exit(int(os.environ.get('WT_FETCH_CODE', '0')))\n"
            "if command[:2] == ['remote', 'set-head']:\n"
            "    if os.environ.get('WT_HEAD_CODE'):\n"
            "        sys.exit(int(os.environ['WT_HEAD_CODE']))\n"
            "    remote = command[2]\n"
            "    sys.exit(subprocess.call([real_git, *prefix, 'symbolic-ref', "
            "f'refs/remotes/{remote}/HEAD', f'refs/remotes/{remote}/main']))\n"
            "os.execv(real_git, [real_git, *args])\n"
        )

    def env(self, **extra: str) -> dict[str, str]:
        env = env_for(self.world)
        return {**env, "PATH": f"{self.bin}{os.pathsep}{env['PATH']}", **extra}

    def answer(self, responses: dict) -> None:
        import json

        self.responses.write_text(json.dumps(responses))

    def calls(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        found: list[list[str]] = []
        call: list[str] = []
        for arg in os.fsdecode(self.log.read_bytes()).split("\0")[:-1]:
            if arg == END:
                found.append(call)
                call = []
            else:
                call.append(arg)
        return found

    def network_calls(self) -> list[list[str]]:
        found = []
        for call in self.calls():
            tool, *args = call
            if tool == "git" and args[:1] == ["-C"]:
                args = args[2:]
            if (
                tool in ("gh", "glab")
                or args[:1] == ["fetch"]
                or args[:2] == ["remote", "set-head"]
            ):
                found.append(call)
        return found


@pytest.fixture
def network(world):
    return Network(world)


# Real shells, partial clones and the stand-in forge cost seconds a test.
# `pytest tests` skips them; `pytest tests -m integration` runs them, and CI
# runs both.
INTEGRATION_MODULES = ("test_shims", "test_partial_clone")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        fixtures = getattr(item, "fixturenames", ())
        module = getattr(getattr(item, "module", None), "__name__", "")
        if (
            "network" in fixtures
            or "forge_world" in fixtures
            or module in INTEGRATION_MODULES
        ):
            item.add_marker(pytest.mark.integration)
