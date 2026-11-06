function gw --wraps gw --description 'cd to where gw lands you'
    if test (count $argv) -ne 1; or string match -q -- '-*' "$argv[1]"
        command gw $argv
        return $status
    end
    set -l commands (command gw --complete | string replace -r '\t.*$' '')
    if contains -- "$argv[1]" $commands
        command gw $argv
        return $status
    end
    # Keep every path byte; remove only the destination record delimiter.
    set -l output (command gw $argv | string collect -N)
    set -l code $pipestatus[1]
    set -l dest (string split -r -m1 \n -- "$output")[1]
    if test $code -eq 0; and string match -q -- '/*' "$dest"; and test -d "$dest"
        cd -- "$dest"; or return $status
    else
        printf '%s' "$output"
    end
    return $code
end
