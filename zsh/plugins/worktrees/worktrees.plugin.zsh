# The four commands that change the caller's directory.
#
# Everything else here is a console script and needs nothing: gws, gwp, gwnb
# and gwrot answer the same from a prompt and from a script. A binary cannot
# cd its caller, and that is the only reason this file exists.
#
# $PATH is the package's business. `uv tool install` puts the binaries in
# ~/.local/bin; nothing here adds a directory to it.

0="${${ZERO:-${0:#$ZSH_ARGZERO}}:-${(%):-%N}}"
0="${${(M)0:#/*}:-$PWD/$0}"

fpath+=("${0:h}/functions" "${0:h}/completions")
autoload -Uz gwa gwl gwm gwr
