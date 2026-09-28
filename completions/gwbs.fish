complete -c gwbs -f
complete -c gwbs -a '(command gwbs --complete)'

complete -c gwbs -l no-fetch -d 'use the refs already here'
complete -c gwbs -l no-forge -d 'never ask GitHub or GitLab'
complete -c gwbs -l json -d 'verdicts and evidence as data'
complete -c gwbs -s q -l quiet -d 'omit the summary'
complete -c gwbs -s v -l verbose -d 'print every command'
complete -c gwbs -l explain -d 'print every git command and exit'
complete -c gwbs -s h -l help -d 'show the help and exit'
