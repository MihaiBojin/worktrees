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

Measured 2026-09-12T08:43:43Z against `d18bc71`, version 0.2.3, on macOS-26.6.2-arm64-arm-64bit-Mach-O. 50 runs each, wall clock divided, after 3 discarded.

|  | 3.11.13 | 3.12.13 | 3.13.7 | 3.14.7 |
| --- | --- | --- | --- | --- |
| the interpreter alone | 16.8 ms | 24.2 ms | 22.7 ms | 25.5 ms |
| gw version | 42.7 ms | 56.3 ms | 55.6 ms | 62.6 ms |
| gwh | 41.0 ms | 54.2 ms | 54.6 ms | 60.6 ms |
| gws --help | 42.0 ms | 56.7 ms | 54.6 ms | 63.8 ms |
| /bin/echo, for scale | 2.5 ms | 2.4 ms | 2.5 ms | 2.6 ms |

### 3.11.13, the 8 costliest imports

| module | self | cumulative |
| --- | --- | --- |
| `worktrees.git` | 1.1 ms | 4.7 ms |
| `worktrees.repo` | 1.1 ms | 5.8 ms |
| `typing` | 1.1 ms | 1.1 ms |
| `inspect` | 0.9 ms | 3.6 ms |
| `enum` | 0.9 ms | 0.9 ms |
| `subprocess` | 0.8 ms | 2.3 ms |
| `worktrees.rotate` | 0.8 ms | 1.3 ms |
| `ipaddress` | 0.6 ms | 0.6 ms |

### 3.12.13, the 8 costliest imports

| module | self | cumulative |
| --- | --- | --- |
| `worktrees.repo` | 1.6 ms | 7.7 ms |
| `typing` | 1.4 ms | 1.4 ms |
| `worktrees.git` | 1.3 ms | 6.2 ms |
| `inspect` | 1.2 ms | 5.0 ms |
| `worktrees.rotate` | 1.0 ms | 1.7 ms |
| `enum` | 0.8 ms | 0.8 ms |
| `ast` | 0.8 ms | 1.5 ms |
| `ipaddress` | 0.7 ms | 0.7 ms |

### 3.13.7, the 8 costliest imports

| module | self | cumulative |
| --- | --- | --- |
| `worktrees.git` | 1.4 ms | 6.6 ms |
| `typing` | 1.2 ms | 1.2 ms |
| `inspect` | 1.2 ms | 4.6 ms |
| `worktrees.repo` | 1.1 ms | 7.7 ms |
| `worktrees.rotate` | 0.8 ms | 1.4 ms |
| `enum` | 0.8 ms | 0.8 ms |
| `time` | 0.7 ms | 0.7 ms |
| `dis` | 0.7 ms | 1.5 ms |

### 3.14.7, the 8 costliest imports

| module | self | cumulative |
| --- | --- | --- |
| `typing` | 1.6 ms | 1.6 ms |
| `worktrees.repo` | 1.4 ms | 9.2 ms |
| `worktrees.git` | 1.4 ms | 7.9 ms |
| `inspect` | 1.3 ms | 5.2 ms |
| `enum` | 0.9 ms | 0.9 ms |
| `argparse` | 0.9 ms | 1.0 ms |
| `_ast` | 0.8 ms | 0.8 ms |
| `worktrees.rotate` | 0.8 ms | 1.0 ms |

