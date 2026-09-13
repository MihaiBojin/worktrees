# scripts

| | |
| --- | --- |
| `benchmark.py` | what an invocation costs, on every Python this package supports |
| `verify-publish.bash` | reads back what a release published |

## Benchmarking

```console
uv run scripts/benchmark.py > b.json
uv run scripts/benchmark.py --markdown --from b.json > docs/BENCHMARK.md
uv run scripts/benchmark.py --runs 5 --python 3.14      # one version, quickly
```

Starting is the only thing worth timing. Everything else this program does is
a `git` subprocess or a forge round trip, and both are facts about a
repository and a network: a monorepo and a five-file repository do not agree,
and neither do two runs on the same laptop. Where a count says something a
clock cannot, `gws --explain` lists the git commands and `-v` prints the ones
a run issued.

### Every interpreter comes from uv

`uv tool install --managed-python` is what the script passes, and it is the
whole reason the columns can be compared.

Without it `uv` takes whatever interpreter it finds first. On one machine
that was Homebrew's 3.14, python.org's 3.13, Homebrew's 3.12 and a uv
download for 3.11: four packagers, measured as though they were four
versions. Homebrew's 3.14 `dlopen`s 76 extension modules from `lib-dynload`
where uv's builds link all but two into the executable, and on macOS every
`dlopen` pays a code-signature check. `import math` cost 0.51 ms under one
and 0.03 ms under the other, and the range across versions read as 42.7 ms to
62.6 ms rather than the 48.4 ms to 55.6 ms it is.

Each column records its own `builtin_modules` and `dynload_objects`, and the
generated table prints both, so a row that looks like a version difference
can be checked against the build rather than believed.

`.github/workflows/tests.yml` is uv-only for the same reason: `setup-uv` with
`python-version` runs `uv python install`, so each matrix entry downloads a
`cpython-X.Y.Z-linux-x86_64-gnu` build rather than using whatever the runner
image ships.

`uv` downloads an interpreter it does not have, so a cold machine reaches the
network on the first run. Nothing else in the script does.

### What the JSON holds

Every module `-X importtime` named, with `self` and `cumulative` microseconds
and the nesting depth, rather than a chosen few. A later question about one
of them is then a filter rather than another run:

```console
$ python3 -c "$(cat <<'EOF'
import json, sys
rows = json.load(sys.stdin)["pythons"][-1]["imports"]
for i, r in enumerate(rows):
    if r["module"] == "ipaddress":
        parent = next(rows[j]["module"] for j in range(i + 1, len(rows))
                      if rows[j]["depth"] < r["depth"])
        print(r["module"], "<-", parent)
EOF
)" < b.json
ipaddress <- urllib.parse
```

`--markdown` is one view of it. Write another rather than recording less.

### The figures move with the machine

`docs/BENCHMARK.md` is the last run, stamped with the commit, the version and
the platform. It is not a target and nothing checks it: an arm64 laptop and an
x86 runner do not agree, and neither does the same laptop on mains and on
battery. Regenerate it when a change is meant to move it.
