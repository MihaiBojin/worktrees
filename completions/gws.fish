# Flags as `gws --help` lists them.

complete -c gws -f

complete -c gws -l branch -d 'consider only that branch'
complete -c gws -l no-fetch -d 'use the refs already here'
complete -c gws -l delete-ignored -d 'count gitignored files as removable'
complete -c gws -l no-forge -d 'decide from git alone; never ask the forge'

complete -c gws -l json -d 'verdicts as data'
complete -c gws -s q -l quiet -d 'verdicts only'
complete -c gws -s v -l verbose -d 'print every git command'
complete -c gws -l explain -d 'print every git command and exit'

complete -c gws -l version -d 'show the version and exit'
complete -c gws -s h -l help -d 'show the help and exit'
