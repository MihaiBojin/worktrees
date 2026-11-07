# Candidates come from the CLI. `gwr --complete` prints the worktrees gwr
# can reach, which is not gw list's: the main checkout is never one.

complete -c gwr -f
complete -c gwr -a '(command gwr --complete)'

complete -c gwr -l keep-branch -d 'remove the worktree, finished or not, and keep its branch and stashes'
complete -c gwr -l delete -x -a 'empty ignored-files stashes all' -d 'also delete this; nothing restores it'
complete -c gwr -s y -l yes -d 'do not ask'

complete -c gwr -l json -d 'the result as data'
complete -c gwr -s q -l quiet -d 'the path alone'
complete -c gwr -s v -l verbose -d 'print every git and forge command'
complete -c gwr -l explain -d 'print every git command and exit'

complete -c gwr -s h -l help -d 'show the help and exit'
