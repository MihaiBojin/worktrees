complete -c gwbd -f
complete -c gwbd -a '(command gwbd --complete)'

complete -c gwbd -l no-fetch -d 'use the refs already here'
complete -c gwbd -l force-refresh -d 'fetch even when the last fetch is under 10 minutes old'
complete -c gwbd -l no-forge -d 'never ask GitHub or GitLab'
complete -c gwbd -l json -d 'verdicts and evidence as data'
complete -c gwbd -s q -l quiet -d 'omit the summary'
complete -c gwbd -s v -l verbose -d 'print every command'
complete -c gwbd -l explain -d 'print every git command and exit'
complete -c gwbd -s h -l help -d 'show the help and exit'
complete -c gwbd -l all -d 'assess all local branches for deletion'
complete -c gwbd -l dry-run -d 'report without deleting'
complete -c gwbd -s y -l yes -d 'skip deletion confirmation'
