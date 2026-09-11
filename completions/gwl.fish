# Candidates come from the CLI. `gwl --complete` prints every worktree
# as name and path, and the shell narrows: no ranking lives here.

complete -c gwl -f
complete -c gwl -a '(command gwl --complete)'

complete -c gwl -s l -l list -d 'print them all and pick none'

complete -c gwl -l json -d 'the result as data'
complete -c gwl -s q -l quiet -d 'the path alone'
complete -c gwl -s v -l verbose -d 'print every git command'
complete -c gwl -l explain -d 'print every git command and exit'

complete -c gwl -l version -d 'show the version and exit'
complete -c gwl -s h -l help -d 'show the help and exit'
