function gwl --wraps gwl --description 'cd to where gwl lands you'
    # Keep every path byte; remove only the destination record delimiter.
    set -l output (command gwl $argv | string collect -N)
    set -l code $pipestatus[1]
    set -l dest (string split -r -m1 \n -- "$output")[1]
    if test $code -eq 0; and test -n "$dest"; and test -d "$dest"
        cd -- "$dest"; or return $status
    else
        printf '%s' "$output"
    end
    return $code
end
