function gw --wraps gw --description 'cd to where gw lands you'
    set -l navigation 0
    test "$argv[1]" = list; and set navigation 1
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
