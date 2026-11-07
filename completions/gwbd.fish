complete -c gwbd -f
complete -c gwbd -a '(command gwbd --complete)'

complete -c gwbd -l json -d 'verdicts and evidence as data'
complete -c gwbd -s q -l quiet -d 'omit the summary'
complete -c gwbd -s v -l verbose -d 'print every git and forge command'
complete -c gwbd -l explain -d 'print every git command and exit'
complete -c gwbd -s h -l help -d 'show the help and exit'
complete -c gwbd -l all -d 'assess all local branches for deletion'
complete -c gwbd -l dry-run -d 'report without deleting'
complete -c gwbd -l delete -x -a 'empty stashes all' -d 'also delete a branch VALUE covers'
complete -c gwbd -s y -l yes -d 'skip deletion confirmation'
