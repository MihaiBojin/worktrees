# The four commands that change the caller's directory, and two shortcuts:
# gwr!, which is gwr --delete-ignored, and gwp!, which is gwp
# --force-refresh --delete-ignored --yes.
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
autoload -Uz gwa gwl gwm gwr 'gwr!' 'gwp!'

# The release this plugin came from, which `gwh` and `gw version` compare
# with their own. Exported, because the environment is all a binary can see
# of the shell that started it.
export WORKTREES_PLUGIN_VERSION=0.6.2
