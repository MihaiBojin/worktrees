#!/usr/bin/env python3
"""What an invocation costs, on every Python this package supports.

Everything else this program does is a `git` subprocess or a forge round
trip, and both are facts about a repository and a network rather than about
the code. Starting is the one cost that belongs to the code, and it is paid
once per command somebody types.

    uv run scripts/benchmark.py                 # JSON on stdout
    uv run scripts/benchmark.py --markdown      # a table, from that JSON
    uv run scripts/benchmark.py > b.json
    uv run scripts/benchmark.py --markdown --from b.json

The JSON carries every module `-X importtime` reported, not a chosen few, so
a later question about one of them is answered by filtering rather than by
measuring again. `--markdown` is one view of it; write another rather than
narrowing what is recorded.

It measures the wheel, installed with `uv tool install`, which is how the
README says to install it. An editable checkout has a different `site` and
answers a different question.

`uv` downloads an interpreter it does not have, so the first run on a cold
machine reaches the network. Nothing else here does.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

# The cheapest invocation of each. Every one of them parses arguments and
# prints, and none touches git, so what they measure is starting.
COMMANDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("gw version", ("gw", "version")),
    ("gwh", ("gwh",)),
    ("gws --help", ("gws", "--help")),
)


def supported_pythons() -> list[str]:
    """The versions pyproject.toml declares, so there is one list to keep."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    found = []
    for row in data["project"]["classifiers"]:
        prefix = "Programming Language :: Python :: "
        if row.startswith(prefix):
            rest = row[len(prefix) :]
            if "." in rest:
                found.append(rest)
    return found


def _run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, check=True, **kwargs)


WARMUP = 3


def wall_ms(argv: list[str], runs: int) -> float:
    """Whole process, `runs` times, divided.

    Whole process because that is what somebody waits for: an in-process
    timer around an import excludes starting and charges nothing for a module
    already loaded. One run is noise, so it is never one run.

    The first few are thrown away. A freshly installed wheel is cold on disk
    and macOS verifies its signature once, which put the first invocation at
    167 ms against a settled 69 ms, so a small --runs would otherwise measure
    the install rather than the program.
    """
    for _ in range(WARMUP):
        subprocess.run(argv, capture_output=True, check=False)
    started = time.perf_counter()
    for _ in range(runs):
        subprocess.run(argv, capture_output=True, check=False)
    return (time.perf_counter() - started) * 1000 / runs


def import_table(python: Path) -> list[dict[str, Any]]:
    """Every module `-X importtime` names, with its own cost and its children's.

    `cumulative` includes children, so the rows do not add up to the total
    and the two columns answer different questions: `self` says what a module
    costs, `cumulative` says what importing it costs.
    """
    proc = subprocess.run(
        [str(python), "-X", "importtime", "-c", "import worktrees.cli"],
        capture_output=True,
        text=True,
        check=False,
    )
    rows = []
    for line in proc.stderr.splitlines():
        if not line.startswith("import time:"):
            continue
        parts = line[len("import time:") :].split("|")
        if len(parts) != 3 or not parts[0].strip().isdigit():
            continue
        # The third field is indented two spaces per level and says what
        # imported what, so it is measured before it is stripped.
        name = parts[2].rstrip()
        rows.append(
            {
                "module": name.strip(),
                "depth": (len(name) - len(name.lstrip())) // 2,
                "self_us": int(parts[0].strip()),
                "cumulative_us": int(parts[1].strip()),
            }
        )
    return rows


def build_wheel(out: Path) -> Path:
    """One wheel, installed under every interpreter, so the only variable is
    the interpreter."""
    _run(["uv", "build", "--wheel", "--out-dir", str(out)], cwd=str(ROOT))
    wheels = sorted(out.glob("*.whl"))
    if not wheels:
        raise SystemExit("uv build produced no wheel")
    return wheels[-1]


def measure(wheel: Path, version: str, runs: int, scratch: Path) -> dict[str, Any]:
    """Install under one interpreter and time it."""
    tools, binaries = scratch / f"tools-{version}", scratch / f"bin-{version}"
    env = {
        **os.environ,
        "UV_TOOL_DIR": str(tools),
        "UV_TOOL_BIN_DIR": str(binaries),
    }
    subprocess.run(
        ["uv", "tool", "install", "--quiet", "--python", version, str(wheel)],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    python = tools / "git-worktrees" / "bin" / "python"
    actual = _run([str(python), "--version"]).stdout.strip().split()[-1]

    timings = {
        "the interpreter alone": wall_ms([str(python), "-c", "pass"], runs),
    }
    for label, argv in COMMANDS:
        timings[label] = wall_ms([str(binaries / argv[0]), *argv[1:]], runs)
    if Path("/bin/echo").exists():
        timings["/bin/echo, for scale"] = wall_ms(["/bin/echo", "x"], runs)

    return {
        "requested": version,
        "actual": actual,
        "timings_ms": {k: round(v, 1) for k, v in timings.items()},
        "imports": import_table(python),
    }


def collect(runs: int, versions: list[str]) -> dict[str, Any]:
    head = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    declared = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"][
        "version"
    ]
    with tempfile.TemporaryDirectory(prefix="worktrees-bench-") as tmp:
        scratch = Path(tmp)
        wheel = build_wheel(scratch / "dist")
        pythons = [measure(wheel, v, runs, scratch) for v in versions]
    return {
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "commit": head.stdout.strip(),
        "version": declared,
        "runs": runs,
        "machine": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor() or platform.machine(),
        },
        "pythons": pythons,
    }


def to_markdown(data: dict[str, Any], top: int) -> str:
    """One view of the JSON. Write another rather than recording less."""
    pythons = data["pythons"]
    out = [
        "# What an invocation costs",
        "",
        "Generated. Everything else this program does is a `git` subprocess or",
        "a forge round trip, and both are facts about a repository and a",
        "network rather than about the code, so starting is the only thing",
        "timed here.",
        "",
        "```console",
        "$ uv run scripts/benchmark.py > b.json",
        "$ uv run scripts/benchmark.py --markdown --from b.json > docs/BENCHMARK.md",
        "```",
        "",
        "The JSON keeps every module `-X importtime` named. This is one view of",
        "it, and a question about a module missing below is a filter rather",
        "than another run.",
        "",
        f"Measured {data['measured_at']} against `{data['commit']}`, version "
        f"{data['version']}, on {data['machine']['platform']}. "
        f"{data['runs']} runs each, wall clock divided, after "
        f"{WARMUP} discarded.",
        "",
    ]

    labels: list[str] = []
    for entry in pythons:
        for label in entry["timings_ms"]:
            if label not in labels:
                labels.append(label)
    header = ["", *(p["actual"] for p in pythons)]
    out.append("| " + " | ".join(header) + " |")
    out.append("| " + " | ".join("---" for _ in header) + " |")
    for label in labels:
        cells = [f"{p['timings_ms'].get(label, '')} ms" for p in pythons]
        out.append(f"| {label} | " + " | ".join(cells) + " |")
    out.append("")

    for entry in pythons:
        rows = sorted(entry["imports"], key=lambda r: -r["self_us"])[:top]
        out.append(f"### {entry['actual']}, the {top} costliest imports")
        out.append("")
        out.append("| module | self | cumulative |")
        out.append("| --- | --- | --- |")
        for row in rows:
            out.append(
                f"| `{row['module']}` | {row['self_us'] / 1000:.1f} ms "
                f"| {row['cumulative_us'] / 1000:.1f} ms |"
            )
        out.append("")
    # No trailing blank line: the rendered file is committed, and
    # end-of-file-fixer would rewrite it every time it was regenerated.
    return "\n".join(out).rstrip("\n")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="benchmark",
        description="What an invocation costs, on every supported Python.",
    )
    p.add_argument("--runs", type=int, default=50, help="invocations per figure")
    p.add_argument(
        "--python",
        action="append",
        metavar="X.Y",
        help="measure this version only; repeatable",
    )
    p.add_argument("--markdown", action="store_true", help="render a table")
    p.add_argument(
        "--from",
        dest="source",
        metavar="FILE",
        help="render from a JSON file rather than measuring again",
    )
    p.add_argument(
        "--top", type=int, default=12, help="imports per version in --markdown"
    )
    args = p.parse_args(argv)

    if args.source:
        data = json.loads(Path(args.source).read_text())
    else:
        if not shutil.which("uv"):
            print("uv is not on PATH; this drives it", file=sys.stderr)
            return 2
        versions = args.python or supported_pythons()
        if not versions:
            print("pyproject.toml declares no Python versions", file=sys.stderr)
            return 2
        print(f"measuring {', '.join(versions)} ...", file=sys.stderr)
        data = collect(args.runs, versions)

    if args.markdown:
        print(to_markdown(data, args.top))
    else:
        print(json.dumps(data, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
