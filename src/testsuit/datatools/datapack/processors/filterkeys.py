#!/usr/bin/env python3

import os
import sys
import re

script_name = os.path.basename(__file__)
script_dir = os.path.dirname(os.path.abspath(__file__))


def main(args):
    if len(args) < 2:
        print(f"Usage: {script_name} <TARGET_FILE> <grepArguments>", file=sys.stderr)
        sys.exit(1)

    TARGET_FILE = args[0]
    grepArguments = args[1]

    pattern = re.compile(grepArguments)

    with open(TARGET_FILE) as f:
        lines = f.readlines()

    filtered = [line for line in lines if pattern.search(line) and "metadata" not in line]

    with open(TARGET_FILE + ".tmp", "w") as f:
        f.writelines(filtered)

    os.rename(TARGET_FILE + ".tmp", TARGET_FILE)


if __name__ == "__main__":
    main(sys.argv[1:])