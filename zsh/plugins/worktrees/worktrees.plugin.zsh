# The four commands that change the caller's directory, and three
# shortcuts: gwr!, which is gwr --delete ignored-files; gwp!, which is gwp
# --yes; and gwp!!, which is gwp --delete all --yes.
#
# Everything else here is a console script and needs nothing: gws, gwp, gwbc
# and gwrot answer the same from a prompt and from a script. A binary cannot
# cd its caller, and that is the reason this file exists.
#
# $PATH is the package's business. `uv tool install` puts the binaries in
# ~/.local/bin; nothing here adds a directory to it.

0="${${ZERO:-${0:#$ZSH_ARGZERO}}:-${(%):-%N}}"
0="${${(M)0:#/*}:-$PWD/$0}"

fpath+=("${0:h}/functions" "${0:h}/completions")
autoload -Uz gw gwa gwm gwr 'gwr!' 'gwp!' 'gwp!!'

# The release this plugin came from, which `gw -h` and `gw version` compare
# with their own. Exported, because the environment is all a binary can see
# of the shell that started it.
export WORKTREES_PLUGIN_VERSION=0.7.0
