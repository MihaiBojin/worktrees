# Candidates come from the CLI. `gwr --complete` prints the worktrees gwr
# can reach, which is not gwl's list: the main checkout is never one.

complete -c gwr -f
complete -c gwr -a '(command gwr --complete)'

complete -c gwr -s f -l force -d 'remove it even when unfinished'
complete -c gwr -l delete-ignored -d 'also delete its gitignored files'
complete -c gwr -l no-fetch -d 'use the refs already here'
complete -c gwr -l no-forge -d 'decide from git alone; never ask the forge'
complete -c gwr -s y -l yes -d 'do not ask'

complete -c gwr -l json -d 'the result as data'
complete -c gwr -s q -l quiet -d 'the path alone'
complete -c gwr -s v -l verbose -d 'print every git command'
complete -c gwr -l explain -d 'print every git command and exit'

complete -c gwr -s h -l help -d 'show the help and exit'
