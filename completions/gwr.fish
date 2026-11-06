# Candidates come from the CLI. `gwr --complete` prints the worktrees gwr
# can reach, which is not gwl's list: the main checkout is never one.

complete -c gwr -f
complete -c gwr -a '(command gwr --complete)'

complete -c gwr -s y -l yes -d 'do not ask'

complete -c gwr -l json -d 'the result as data'
complete -c gwr -s q -l quiet -d 'the path alone'
complete -c gwr -s v -l verbose -d 'print every git command'
complete -c gwr -l explain -d 'print every git command and exit'

complete -c gwr -s h -l help -d 'show the help and exit'
complete -c gwr -l delete -r -d 'permit deletion categories; repeat to combine'
complete -c gwr -l keep-branch -d 'retain the branch and its stashes'
