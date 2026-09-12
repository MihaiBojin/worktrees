"""The benchmark's parsing and rendering, without running a benchmark.

Measuring takes a minute and installs four interpreters, so what is asserted
here is the half that can be wrong quietly: which versions it measures, what
it makes of `-X importtime`, and what the table says.
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import benchmark  # noqa: E402

IMPORTTIME = """\
import time: self [us] | cumulative | imported package
import time:       124 |        124 | _io
import time:       299 |        760 |   difflib
import time:      5483 |      28169 | worktrees.cli
"""


def test_it_measures_every_version_pyproject_declares() -> None:
    """One list, so a Python added to the classifiers is measured by that."""
    declared = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    classifiers = [
        row.rsplit(" :: ", 1)[-1]
        for row in declared["classifiers"]
        if row.startswith("Programming Language :: Python :: ") and "." in row
    ]
    assert benchmark.supported_pythons() == classifiers
    assert classifiers, "pyproject.toml names no Python versions"


def test_importtime_is_read_as_two_different_questions(tmp_path, monkeypatch) -> None:
    """`self` is what a module costs, `cumulative` is what importing it costs.

    They are not interchangeable and the rows do not add up, so both are
    recorded rather than one being derived from the other.
    """
    fake = tmp_path / "python"
    fake.write_text(f"#!/bin/sh\ncat >&2 <<'EOF'\n{IMPORTTIME}EOF\n")
    fake.chmod(0o755)

    rows = benchmark.import_table(fake)
    assert [r["module"] for r in rows] == ["_io", "difflib", "worktrees.cli"]
    assert rows[1] == {
        "module": "difflib",
        "depth": 1,
        "self_us": 299,
        "cumulative_us": 760,
    }
    assert rows[2]["cumulative_us"] > rows[2]["self_us"]


def test_the_header_line_is_not_read_as_a_module(tmp_path) -> None:
    """`-X importtime` leads with a header whose first field is `self [us]`."""
    fake = tmp_path / "python"
    fake.write_text(f"#!/bin/sh\ncat >&2 <<'EOF'\n{IMPORTTIME}EOF\n")
    fake.chmod(0o755)
    assert all(
        row["module"] != "imported package" for row in benchmark.import_table(fake)
    )


def _sample() -> dict:
    return {
        "measured_at": "2026-09-12T08:00:00Z",
        "commit": "abc1234",
        "version": "9.9.9",
        "runs": 50,
        "machine": {"platform": "macOS-26", "machine": "arm64", "processor": "arm"},
        "pythons": [
            {
                "requested": "3.11",
                "actual": "3.11.13",
                "timings_ms": {"the interpreter alone": 15.4, "gw version": 44.4},
                "imports": [
                    {"module": "a", "depth": 0, "self_us": 900, "cumulative_us": 900},
                    {"module": "b", "depth": 0, "self_us": 100, "cumulative_us": 100},
                ],
            },
            {
                "requested": "3.14",
                "actual": "3.14.7",
                "timings_ms": {"the interpreter alone": 23.3, "gw version": 69.0},
                "imports": [
                    {"module": "a", "depth": 0, "self_us": 800, "cumulative_us": 800}
                ],
            },
        ],
    }


def test_the_table_puts_one_version_per_column() -> None:
    text = benchmark.to_markdown(_sample(), top=1)
    assert "| 3.11.13 | 3.14.7 |" in text
    assert "| the interpreter alone | 15.4 ms | 23.3 ms |" in text
    assert "| gw version | 44.4 ms | 69.0 ms |" in text
    assert "abc1234" in text and "9.9.9" in text


def test_top_narrows_the_view_and_not_the_record() -> None:
    """The JSON keeps every module; --top only decides what the table shows."""
    data = _sample()
    text = benchmark.to_markdown(data, top=1)
    assert "`a`" in text
    assert "`b`" not in text
    assert len(data["pythons"][0]["imports"]) == 2


def test_it_renders_from_a_file_rather_than_measuring(tmp_path, capsys) -> None:
    """`--from` is what makes a second question cost no second run."""
    path = tmp_path / "b.json"
    path.write_text(json.dumps(_sample()))
    assert benchmark.main(["--markdown", "--from", str(path)]) == 0
    assert "| 3.11.13 | 3.14.7 |" in capsys.readouterr().out
