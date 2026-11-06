# Flags as `gwp --help` lists them.

complete -c gwp -f

complete -c gwp -l list -d 'assess without prompting; uncoloured table'
complete -c gwp -l only -x -a 'remove empty keep unknown' -d 'filter assessment; rejects --yes'
complete -c gwp -s y -l yes -d 'approve removal, including with --json or --list'

complete -c gwp -l json -d 'assess as JSON; --yes approves removal'
complete -c gwp -s q -l quiet -d 'verdicts only'
complete -c gwp -s v -l verbose -d 'print every git command'
complete -c gwp -l explain -d 'print every git command and exit'

complete -c gwp -s h -l help -d 'show the help and exit'
complete -c gwp -l delete -x -a 'empty ignored-files stashes all' -d 'permit deletion categories; repeat to combine'
