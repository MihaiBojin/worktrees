# Flags as `gwrot --help` lists them.

complete -c gwrot -f

complete -c gwrot -l no-fetch -d 'work from what is already here'

complete -c gwrot -l json -d 'the result as data'
complete -c gwrot -s q -l quiet -d 'say nothing on success'
complete -c gwrot -s v -l verbose -d 'print every git command'
complete -c gwrot -l explain -d 'print every git command and exit'

complete -c gwrot -s h -l help -d 'show the help and exit'
