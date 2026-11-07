# Flags as `gwp --help` lists them.

complete -c gwp -f

complete -c gwp -l delete -x -a 'ignored-files all' -d 'make a worktree eligible though VALUE goes with it'
complete -c gwp -l only -x -a 'remove keep unknown' -d 'narrow the table; what is removed stays the same'
complete -c gwp -s l -l list -d 'print the table uncoloured and ask nothing'
complete -c gwp -s y -l yes -d 'do not ask before removing'

complete -c gwp -l json -d 'verdicts as data'
complete -c gwp -s q -l quiet -d 'verdicts only'
complete -c gwp -s v -l verbose -d 'print every git command'
complete -c gwp -l explain -d 'print every git command and exit'

complete -c gwp -s h -l help -d 'show the help and exit'
