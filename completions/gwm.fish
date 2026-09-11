# Flags as `gwm --help` lists them.

complete -c gwm -f

complete -c gwm -l json -d 'the result as data'
complete -c gwm -s q -l quiet -d 'the path alone'
complete -c gwm -s v -l verbose -d 'print every git command'
complete -c gwm -l explain -d 'print every git command and exit'

complete -c gwm -s h -l help -d 'show the help and exit'
