"""Print the target's rules with missing Worktrees rules appended."""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path


def literal_rules(text: str) -> list[tuple[object, object, str]]:
    rules = []
    for node in ast.parse(text).body:
        if not (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "prefix_rule"
        ):
            continue
        fields = {kw.arg: kw.value for kw in node.value.keywords}
        try:
            pattern = ast.literal_eval(fields["pattern"])
            decision = (
                ast.literal_eval(fields["decision"])
                if "decision" in fields
                else "allow"
            )
        except (KeyError, ValueError, TypeError):
            continue
        rules.append((pattern, decision, ast.get_source_segment(text, node) or ""))
    return rules


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path)
    target = parser.parse_args().target
    source = Path(__file__).with_name("worktrees.rules")
    try:
        try:
            held = target.read_bytes().decode("utf-8")
        except FileNotFoundError:
            held = ""
        existing = [(pattern, decision) for pattern, decision, _ in literal_rules(held)]
        wanted = source.read_text(encoding="utf-8")
        missing = [
            code
            for pattern, decision, code in literal_rules(wanted)
            if (pattern, decision) not in existing
        ]
    except (OSError, UnicodeError, SyntaxError) as exc:
        print(f"Cannot prepare rules for {target}: {exc}", file=sys.stderr)
        return 1
    if missing:
        held += ("\n\n" if held else "") + "\n".join(missing) + "\n"
    sys.stdout.buffer.write(held.encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
