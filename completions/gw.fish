# Flags as `gw <command> --help` lists them, and candidates from the CLI.
# A shortcut such as gwa wraps `gw add`, so fish completes it from these.

# `__worktrees_on add a` holds on `gw add ...` and `gw a ...`.
function __worktrees_on
    set -l tokens (commandline -opc)
    contains -- "$tokens[2]" $argv
end

# `__worktrees_on_branch status` holds on `gw branch status ...`.
function __worktrees_on_branch
    set -l tokens (commandline -opc)
    test "$tokens[2]" = branch; and test "$tokens[3]" = "$argv[1]"
end

complete -c gw -f
complete -c gw -n 'test (count (commandline -opc)) -eq 1' -a '(command gw --complete)'
complete -c gw -n 'test (count (commandline -opc)) -eq 1' -s h -l help -d 'show the help and exit'

complete -c gw -n '__worktrees_on sync' -l json -d 'sync state as data'
complete -c gw -n '__worktrees_on sync' -s q -l quiet -d 'omit the summary'
complete -c gw -n '__worktrees_on sync' -s v -l verbose -d 'print every command'
complete -c gw -n '__worktrees_on sync' -s h -l help -d 'show the help and exit'

complete -c gw -n '__worktrees_on version v' -s h -l help -d 'show the help and exit'

complete -c gw -n '__worktrees_on prune p' -l table -d 'assess without prompting; uncoloured table'
complete -c gw -n '__worktrees_on prune p' -l only -x -a 'remove empty keep unknown' -d 'filter assessment; rejects --yes'
complete -c gw -n '__worktrees_on prune p' -s y -l yes -d 'approve removal, including with --json or --table'
complete -c gw -n '__worktrees_on prune p' -l delete -x -a 'empty ignored-files stashes all' -d 'permit deletion categories; repeat to combine'
complete -c gw -n '__worktrees_on prune p' -l json -d 'assess as JSON; --yes approves removal'
complete -c gw -n '__worktrees_on prune p' -s q -l quiet -d 'verdicts only'
complete -c gw -n '__worktrees_on prune p' -s v -l verbose -d 'print every git command'
complete -c gw -n '__worktrees_on prune p' -l explain -d 'print every git command and exit'
complete -c gw -n '__worktrees_on prune p' -s h -l help -d 'show the help and exit'

complete -c gw -n '__worktrees_on add a' -l json -d 'the result as data'
complete -c gw -n '__worktrees_on add a' -s q -l quiet -d 'the path alone'
complete -c gw -n '__worktrees_on add a' -s v -l verbose -d 'print every git command'
complete -c gw -n '__worktrees_on add a' -l explain -d 'print every git command and exit'
complete -c gw -n '__worktrees_on add a' -s h -l help -d 'show the help and exit'

complete -c gw -n '__worktrees_on list l' -a '(command gw list --complete)'
complete -c gw -n '__worktrees_on list l' -l table -d 'print them all and pick none'
complete -c gw -n '__worktrees_on list l' -l json -d 'the result as data'
complete -c gw -n '__worktrees_on list l' -l explain -d 'print every git command and exit'
complete -c gw -n '__worktrees_on list l' -s h -l help -d 'show the help and exit'

# `gw remove --complete` prints the worktrees remove can reach, which is not
# list's list: the main checkout is never one.
complete -c gw -n '__worktrees_on remove rm' -a '(command gw remove --complete)'
complete -c gw -n '__worktrees_on remove rm' -l keep-branch -d 'retain the branch and its stashes'
complete -c gw -n '__worktrees_on remove rm' -l delete -x -a 'empty ignored-files stashes all' -d 'permit deletion categories; repeat to combine'
complete -c gw -n '__worktrees_on remove rm' -l table -d 'assess without prompting; uncoloured table'
complete -c gw -n '__worktrees_on remove rm' -s y -l yes -d 'approve removal, including with --json or --table'
complete -c gw -n '__worktrees_on remove rm' -l json -d 'assess as JSON; --yes approves removal'
complete -c gw -n '__worktrees_on remove rm' -s q -l quiet -d 'verdicts only'
complete -c gw -n '__worktrees_on remove rm' -s v -l verbose -d 'print every git command'
complete -c gw -n '__worktrees_on remove rm' -l explain -d 'print every git command and exit'
complete -c gw -n '__worktrees_on remove rm' -s h -l help -d 'show the help and exit'

complete -c gw -n '__worktrees_on fast-forward ff' -l from -x -d 'start that branch from this branch or commit'
complete -c gw -n '__worktrees_on fast-forward ff' -l json -d 'the result as data'
complete -c gw -n '__worktrees_on fast-forward ff' -s q -l quiet -d 'omit summaries and progress'
complete -c gw -n '__worktrees_on fast-forward ff' -s v -l verbose -d 'print every git command'
complete -c gw -n '__worktrees_on fast-forward ff' -l explain -d 'print every git command and exit'
complete -c gw -n '__worktrees_on fast-forward ff' -s h -l help -d 'show the help and exit'

complete -c gw -n '__worktrees_on branch; and test (count (commandline -opc)) -eq 2' -a '(command gw branch --complete)'
complete -c gw -n '__worktrees_on branch; and test (count (commandline -opc)) -eq 2' -s h -l help -d 'show the help and exit'

complete -c gw -n '__worktrees_on_branch create' -l json -d 'the result as data'
complete -c gw -n '__worktrees_on_branch create' -s q -l quiet -d 'omit summaries and progress'
complete -c gw -n '__worktrees_on_branch create' -s v -l verbose -d 'print every git command'
complete -c gw -n '__worktrees_on_branch create' -l explain -d 'print every git command and exit'
complete -c gw -n '__worktrees_on_branch create' -s h -l help -d 'show the help and exit'

complete -c gw -n '__worktrees_on_branch status' -a '(command gw branch status --complete)'
complete -c gw -n '__worktrees_on_branch status' -l only -x -a 'remove empty keep unknown' -d 'show only rows with this verdict'
complete -c gw -n '__worktrees_on_branch status' -l json -d 'verdicts and evidence as data'
complete -c gw -n '__worktrees_on_branch status' -s q -l quiet -d 'omit the summary'
complete -c gw -n '__worktrees_on_branch status' -l explain -d 'print every git command and exit'
complete -c gw -n '__worktrees_on_branch status' -s h -l help -d 'show the help and exit'

complete -c gw -n '__worktrees_on_branch prune' -a '(command gw branch prune --complete)'
complete -c gw -n '__worktrees_on_branch prune' -l all -d 'assess all local branches for deletion'
complete -c gw -n '__worktrees_on_branch prune' -l dry-run -d 'report without deleting'
complete -c gw -n '__worktrees_on_branch prune' -s y -l yes -d 'skip deletion confirmation'
complete -c gw -n '__worktrees_on_branch prune' -l delete -x -a 'empty stashes all' -d 'permit deletion categories; repeat to combine'
complete -c gw -n '__worktrees_on_branch prune' -l json -d 'verdicts and evidence as data'
complete -c gw -n '__worktrees_on_branch prune' -s q -l quiet -d 'omit the summary'
complete -c gw -n '__worktrees_on_branch prune' -s v -l verbose -d 'print every command'
complete -c gw -n '__worktrees_on_branch prune' -l explain -d 'print every git command and exit'
complete -c gw -n '__worktrees_on_branch prune' -s h -l help -d 'show the help and exit'
