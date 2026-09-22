# What an invocation costs

Generated. Everything else this program does is a `git` subprocess or
a forge round trip, and both are facts about a repository and a
network rather than about the code, so starting is the only thing
timed here.

```console
$ uv run scripts/benchmark.py > b.json
$ uv run scripts/benchmark.py --markdown --from b.json > docs/BENCHMARK.md
```

The JSON keeps every module `-X importtime` named while `gw version` runs,
which is a command rather than a bare import because argparse builds its
parser when one runs. This is one view of it, and a question about a
module missing below is a filter rather than another run.

Measured 2026-09-22T08:21:15Z against `3b7b937`, version 0.4.3, on macOS-26.6.2-arm64-arm-64bit-Mach-O. 50 runs each, wall clock divided, after 3 discarded.

|  | 3.11.13 | 3.12.14 | 3.13.15 | 3.14.7 |
| --- | --- | --- | --- | --- |
| the interpreter alone | 17.5 ms | 18.2 ms | 19.7 ms | 19.9 ms |
| gw version | 28.2 ms | 30.7 ms | 30.7 ms | 41.5 ms |
| gwh | 28.0 ms | 30.1 ms | 30.1 ms | 40.8 ms |
| gws --help | 29.6 ms | 31.3 ms | 31.0 ms | 41.5 ms |
| /bin/echo, for scale | 2.7 ms | 2.7 ms | 2.8 ms | 2.6 ms |

Every column is a uv-managed interpreter. That is the point of `--managed-python`:
without it uv takes whatever it finds first, and a run compares packagers rather
than versions. Homebrew's 3.14 ships 76 extension modules as shared objects
where the build below ships 2, and on macOS every `dlopen` pays a code-signature
check, which was worth 8 ms of interpreter startup on its own.

| how each was built | 3.11.13 | 3.12.14 | 3.13.15 | 3.14.7 |
| --- | --- | --- | --- | --- |
| modules linked into the executable | 99 | 95 | 97 | 101 |
| modules `dlopen`ed from `lib-dynload` | 4 | 3 | 2 | 2 |

### 3.11.13, the 12 costliest imports

| module | self | cumulative |
| --- | --- | --- |
| `typing` | 1.1 ms | 1.1 ms |
| `enum` | 1.0 ms | 1.0 ms |
| `ipaddress` | 0.6 ms | 0.6 ms |
| `urllib.parse` | 0.6 ms | 1.3 ms |
| `time` | 0.6 ms | 0.6 ms |
| `gettext` | 0.5 ms | 0.5 ms |
| `site` | 0.4 ms | 3.0 ms |
| `locale` | 0.4 ms | 0.5 ms |
| `argparse` | 0.4 ms | 3.0 ms |
| `encodings` | 0.4 ms | 0.8 ms |
| `collections` | 0.4 ms | 0.9 ms |
| `pathlib` | 0.4 ms | 2.3 ms |

### 3.12.14, the 12 costliest imports

| module | self | cumulative |
| --- | --- | --- |
| `typing` | 1.3 ms | 1.3 ms |
| `ipaddress` | 0.7 ms | 0.7 ms |
| `enum` | 0.7 ms | 0.7 ms |
| `urllib.parse` | 0.7 ms | 1.6 ms |
| `time` | 0.7 ms | 0.7 ms |
| `nt` | 0.6 ms | 0.6 ms |
| `encodings` | 0.6 ms | 1.1 ms |
| `site` | 0.5 ms | 3.5 ms |
| `argparse` | 0.5 ms | 3.1 ms |
| `gettext` | 0.5 ms | 0.5 ms |
| `locale` | 0.5 ms | 0.5 ms |
| `collections` | 0.5 ms | 1.0 ms |

### 3.13.15, the 12 costliest imports

| module | self | cumulative |
| --- | --- | --- |
| `typing` | 1.2 ms | 1.2 ms |
| `enum` | 0.8 ms | 0.8 ms |
| `site` | 0.7 ms | 4.4 ms |
| `time` | 0.6 ms | 0.6 ms |
| `argparse` | 0.6 ms | 3.2 ms |
| `pathlib._local` | 0.5 ms | 1.5 ms |
| `locale` | 0.5 ms | 0.6 ms |
| `_collections_abc` | 0.5 ms | 0.5 ms |
| `gettext` | 0.5 ms | 0.5 ms |
| `encodings` | 0.5 ms | 1.1 ms |
| `collections` | 0.4 ms | 0.9 ms |
| `_frozen_importlib_external` | 0.4 ms | 0.7 ms |

### 3.14.7, the 12 costliest imports

| module | self | cumulative |
| --- | --- | --- |
| `_colorize` | 3.3 ms | 8.8 ms |
| `typing` | 1.5 ms | 1.5 ms |
| `inspect` | 1.1 ms | 4.6 ms |
| `_ast` | 0.9 ms | 0.9 ms |
| `enum` | 0.7 ms | 0.7 ms |
| `dis` | 0.7 ms | 1.0 ms |
| `time` | 0.6 ms | 0.6 ms |
| `site` | 0.6 ms | 4.5 ms |
| `contextlib` | 0.6 ms | 2.0 ms |
| `tokenize` | 0.5 ms | 0.7 ms |
| `_collections_abc` | 0.5 ms | 0.5 ms |
| `encodings` | 0.5 ms | 1.3 ms |
