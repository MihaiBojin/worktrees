# Flags as `gwp --help` lists them.

complete -c gwp -f

complete -c gwp -l delete-ignored -d 'count gitignored files as eligible'
complete -c gwp -l only -x -a 'remove keep unknown' -d 'show only rows with this verdict'
complete -c gwp -s l -l list -d 'print the table uncoloured and ask nothing'
complete -c gwp -s y -l yes -d 'do not ask before removing'

complete -c gwp -l json -d 'verdicts as data'
complete -c gwp -s q -l quiet -d 'verdicts only'
complete -c gwp -s v -l verbose -d 'print every git command'
complete -c gwp -l explain -d 'print every git command and exit'

complete -c gwp -s h -l help -d 'show the help and exit'
