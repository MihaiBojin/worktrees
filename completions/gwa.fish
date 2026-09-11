# Flags as `gwa --help` lists them.

complete -c gwa -f

complete -c gwa -l no-fetch -d 'branch from what is already here'

complete -c gwa -l json -d 'the result as data'
complete -c gwa -s q -l quiet -d 'the path alone'
complete -c gwa -s v -l verbose -d 'print every git command'
complete -c gwa -l explain -d 'print every git command and exit'

complete -c gwa -s h -l help -d 'show the help and exit'
