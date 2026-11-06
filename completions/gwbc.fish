# Flags as `gwbc --help` lists them.

complete -c gwbc -f


complete -c gwbc -l json -d 'the result as data'
complete -c gwbc -s q -l quiet -d 'say nothing on success'
complete -c gwbc -s v -l verbose -d 'print every git command'
complete -c gwbc -l explain -d 'print every git command and exit'

complete -c gwbc -s h -l help -d 'show the help and exit'
