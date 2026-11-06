function gw --wraps gw --description 'cd to where gw lands you'
    set -l commands (command gw --complete | string replace -r '\t.*$' '')
    set -l navigation 1
    if test (count $argv) -gt 0; and contains -- "$argv[1]" $commands; and test "$argv[1]" != list
        set navigation 0
    end
    for arg in $argv
        test "$arg" != --; or break
        if contains -- "$arg" --json --list -l --help -h --complete --explain; set navigation 0; end
    end
    if not isatty stdin; or test $navigation -eq 0
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
