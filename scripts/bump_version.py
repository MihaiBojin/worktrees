"""Set the release version in every file that names it.

    python3 scripts/bump_version.py 0.7.0

`uv version` writes `pyproject.toml` and `uv.lock`, and the wheel reads it
from there. The shell plugins carry a copy of their own, because Fisher and
Antidote install them from a git tag rather than from anything built, and the
README's install lines name that tag. The agent plugin's three manifests carry
one too: a marketplace installs it from a tag, and a client sees an update only
when the version moves. `.releasetools.yaml` runs this as the
release's bump, and `tests/test_version.py` fails when a copy disagrees with
`pyproject.toml`.

Every line is checked before anything is written: a line that no longer
matches stops the bump with nothing changed, rather than releasing a version
some copy never heard of.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Each file, and the lines in it that name the release, `{}` where it goes.
STAMPS: dict[str, tuple[str, ...]] = {
    "conf.d/worktrees.fish": ("set -gx WORKTREES_PLUGIN_VERSION {}",),
    "zsh/plugins/worktrees/worktrees.plugin.zsh": (
        "export WORKTREES_PLUGIN_VERSION={}",
    ),
    "plugin/.claude-plugin/plugin.json": ('"version": "{}"',),
    "plugin/.codex-plugin/plugin.json": ('"version": "{}"',),
    "plugin/plugin.json": ('"version": "{}"',),
    "README.md": (
        "MihaiBojin/worktrees@v{}",
        "MihaiBojin/worktrees branch:v{}",
        "worktrees {}: git worktree commands",
        "The shell plugin here is {}, the same release",
    ),
}


def _pattern(template: str) -> re.Pattern[str]:
    """The template with any version where `{}` is."""
    before, after = (re.escape(part) for part in template.split("{}"))
    return re.compile(before + r"[0-9][0-9A-Za-z.+]*" + after)


def stamped(root: Path, version: str) -> dict[Path, str]:
    """Every file's text with `version` written in, or SystemExit naming the
    line that matched nothing."""
    texts = {}
    for name, templates in STAMPS.items():
        path = root / name
        text = path.read_text()
        for template in templates:
            # subn reads a backslash in the replacement as a group reference.
            line = template.format(version).replace("\\", r"\\")
            text, count = _pattern(template).subn(line, text)
            if not count:
                raise SystemExit(f"{name}: no line matches {template!r}")
        texts[path] = text
    return texts


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: bump_version.py VERSION", file=sys.stderr)
        return 2
    (version,) = argv
    texts = stamped(ROOT, version)
    subprocess.run(["uv", "version", version], cwd=ROOT, check=True)
    for path, text in texts.items():
        path.write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
