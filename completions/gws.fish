# Flags as `gws --help` lists them.

complete -c gws -f

complete -c gws -s q -l quiet -d 'say nothing on success'
complete -c gws -s v -l verbose -d 'print every git command'
complete -c gws -l explain -d 'print every git command and exit'

complete -c gws -s h -l help -d 'show the help and exit'
