#!/usr/bin/env python3

import os
import sys
import re

script_name = os.path.basename(__file__)
script_dir = os.path.dirname(os.path.abspath(__file__))


def main(args):
    if len(args) < 2:
        print("Usage: {} <TARGET_FILE> <sedRegex>".format(script_name), file=sys.stderr)
        sys.exit(1)

    TARGET_FILE = args[0]
    sedRegex = args[1]

    # Parse sed -r style regex: s/pattern/replacement/flags
    # Extract the substitution pattern from sed syntax
    match = re.match(r"^s/(.*)/(.*)/([gimux]*)$", sedRegex)
    if not match:
        print("ERROR: replace command failed: invalid sed regex '{}'".format(sedRegex), file=sys.stderr)
        sys.exit(1)

    pattern_str, replacement, flags = match.groups()
    re_flags = 0
    if "i" in flags:
        re_flags |= re.IGNORECASE
    if "m" in flags:
        re_flags |= re.MULTILINE
    if "s" in flags:
        re_flags |= re.DOTALL

    pattern = re.compile(pattern_str, re_flags)
    global_replace = "g" in flags

    with open(TARGET_FILE, "r") as f:
        content = f.read()

    if global_replace:
        result = pattern.sub(replacement, content)
    else:
        result = pattern.sub(replacement, content, count=1)

    with open(TARGET_FILE + ".tmp", "w") as f:
        f.write(result)

    os.rename(TARGET_FILE + ".tmp", TARGET_FILE)


if __name__ == "__main__":
    main(sys.argv[1:])