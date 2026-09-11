# Candidates come from the CLI. `gwl --complete` prints every worktree
# as name and path, and the shell narrows: no ranking lives here.

complete -c gwr -f
complete -c gwr -a '(command gwl --complete)'

complete -c gwr -s f -l force -d 'remove it even when unfinished'
complete -c gwr -l delete-ignored -d 'also delete its gitignored files'
complete -c gwr -l no-fetch -d 'use the refs already here'
complete -c gwr -s y -l yes -d 'do not ask'

complete -c gwr -l json -d 'the result as data'
complete -c gwr -s q -l quiet -d 'the path alone'
complete -c gwr -s v -l verbose -d 'print every git command'
complete -c gwr -l explain -d 'print every git command and exit'

complete -c gwr -l version -d 'show the version and exit'
complete -c gwr -s h -l help -d 'show the help and exit'
