# What an invocation costs

Generated. Everything else this program does is a `git` subprocess or
a forge round trip, and both are facts about a repository and a
network rather than about the code, so starting is the only thing
timed here.

```console
$ uv run scripts/benchmark.py > b.json
$ uv run scripts/benchmark.py --markdown --from b.json > docs/BENCHMARK.md
```

The JSON keeps every module `-X importtime` named. This is one view of
it, and a question about a module missing below is a filter rather
than another run.

Measured 2026-09-12T08:57:26Z against `f3f10a1`, version 0.2.3, on macOS-26.6.2-arm64-arm-64bit-Mach-O. 50 runs each, wall clock divided, after 3 discarded.

|  | 3.11.13 | 3.12.14 | 3.13.15 | 3.14.7 |
| --- | --- | --- | --- | --- |
| the interpreter alone | 22.5 ms | 23.5 ms | 24.1 ms | 25.3 ms |
| gw version | 48.4 ms | 49.6 ms | 50.7 ms | 55.6 ms |
| gwh | 45.4 ms | 48.7 ms | 48.2 ms | 53.8 ms |
| gws --help | 45.8 ms | 50.0 ms | 51.8 ms | 56.4 ms |
| /bin/echo, for scale | 2.7 ms | 2.8 ms | 3.0 ms | 3.1 ms |

Every column is a uv-managed interpreter. That is the point of `--managed-python`:
without it uv takes whatever it finds first, and a run compares packagers rather
than versions. Homebrew's 3.14 ships 76 extension modules as shared objects
where the build below ships 2, and on macOS every `dlopen` pays a code-signature
check, which was worth 8 ms of interpreter startup on its own.

| how each was built | 3.11.13 | 3.12.14 | 3.13.15 | 3.14.7 |
| --- | --- | --- | --- | --- |
| modules linked into the executable | 99 | 95 | 97 | 101 |
| modules `dlopen`ed from `lib-dynload` | 4 | 3 | 2 | 2 |

### 3.11.13, the 8 costliest imports

| module | self | cumulative |
| --- | --- | --- |
| `worktrees.git` | 1.1 ms | 4.8 ms |
| `enum` | 1.1 ms | 1.1 ms |
| `worktrees.repo` | 1.1 ms | 5.8 ms |
| `typing` | 1.0 ms | 1.1 ms |
| `inspect` | 0.8 ms | 3.1 ms |
| `worktrees.rotate` | 0.7 ms | 1.4 ms |
| `subprocess` | 0.7 ms | 2.4 ms |
| `worktrees.cli` | 0.6 ms | 21.3 ms |

### 3.12.14, the 8 costliest imports

| module | self | cumulative |
| --- | --- | --- |
| `worktrees.repo` | 1.8 ms | 6.7 ms |
| `typing` | 1.4 ms | 1.4 ms |
| `worktrees.git` | 1.3 ms | 4.8 ms |
| `inspect` | 1.1 ms | 3.9 ms |
| `enum` | 0.8 ms | 0.8 ms |
| `worktrees.rotate` | 0.8 ms | 1.0 ms |
| `dataclasses` | 0.8 ms | 5.2 ms |
| `worktrees.cli` | 0.7 ms | 23.9 ms |

### 3.13.15, the 8 costliest imports

| module | self | cumulative |
| --- | --- | --- |
| `worktrees.git` | 1.4 ms | 3.7 ms |
| `typing` | 1.3 ms | 1.3 ms |
| `inspect` | 1.2 ms | 5.6 ms |
| `worktrees.repo` | 1.2 ms | 4.9 ms |
| `enum` | 0.8 ms | 0.8 ms |
| `worktrees.rotate` | 0.8 ms | 0.9 ms |
| `argparse` | 0.7 ms | 3.7 ms |
| `ast` | 0.7 ms | 1.3 ms |

### 3.14.7, the 8 costliest imports

| module | self | cumulative |
| --- | --- | --- |
| `typing` | 1.3 ms | 1.3 ms |
| `worktrees.git` | 1.3 ms | 5.4 ms |
| `worktrees.repo` | 1.1 ms | 6.6 ms |
| `inspect` | 1.0 ms | 4.3 ms |
| `worktrees.rotate` | 0.8 ms | 0.9 ms |
| `enum` | 0.8 ms | 0.8 ms |
| `worktrees.cli` | 0.7 ms | 22.8 ms |
| `time` | 0.6 ms | 0.6 ms |
