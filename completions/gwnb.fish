# Flags as `gwnb --help` lists them.

complete -c gwnb -f

complete -c gwnb -l no-fetch -d 'branch off what is already here'

complete -c gwnb -l json -d 'the result as data'
complete -c gwnb -s q -l quiet -d 'say nothing on success'
complete -c gwnb -s v -l verbose -d 'print every git command'
complete -c gwnb -l explain -d 'print every git command and exit'

complete -c gwnb -l version -d 'show the version and exit'
complete -c gwnb -s h -l help -d 'show the help and exit'
