#!/usr/bin/env python3

import os
import sys
import re
from pathlib import Path

script_name = os.path.basename(__file__)
script_dir = os.path.dirname(os.path.abspath(__file__))


def main(args):
    if len(args) < 1:
        print(f"Usage: {script_name} <DATASET_DIR>", file=sys.stderr)
        sys.exit(1)

    DATASET_DIR = args[0]

    targetdir = os.path.realpath(DATASET_DIR)

    dataset_name = os.path.basename(os.path.dirname(targetdir))
    version = os.path.basename(targetdir)

    root = Path(targetdir)
    all_files = [str(f) for f in root.rglob('*') if f.is_file()]
    files = [f for f in all_files if "dataset.dico" not in f]
    nbfiles = len(files)

    dico_path = os.path.join(DATASET_DIR, "dataset.dico")
    with open(dico_path, "a") as f:
        f.write(f"{dataset_name}.path={targetdir}\n")
        f.write(f"{dataset_name}.version={version}\n")
        f.write(f"{dataset_name}.nbfiles={nbfiles}\n")

    git_path = os.path.join(DATASET_DIR, ".git")
    if os.path.isfile(git_path):
        gitignore_path = os.path.join(DATASET_DIR, ".gitignore")
        if not os.path.isfile(gitignore_path):
            print("WARNING: Please create a new tag with a '.gitignore' file containing 'dataset.dico'. ")
            print("         Otherwise, an error will be raised next time because of local file dataset.dico generated during import.")
        else:
            with open(gitignore_path) as g:
                has_entry = any("dataset.dico" in line for line in g)
            if not has_entry:
                print("WARNING: please create a new tag with 'dataset.dico' added to .gitignore file: otherwise an error will be raised next time because of local file dataset.dico generated during import")

    # import dico files but ignore dictionaries depending on a specific configuration
    dico_files = []
    for root, dirs, filenames in os.walk(DATASET_DIR):
        for fname in filenames:
            if fname.endswith(".dico") and fname != "dataset.dico" and not re.search(r"conf", fname, re.IGNORECASE):
                dico_files.append(os.path.join(root, fname))

    for dicofile in sorted(dico_files):
        fileNameToPrint = os.path.relpath(dicofile, DATASET_DIR)
        print(f"          -> importing keys from {fileNameToPrint}")
        with open(dicofile) as src, open(dico_path, "a") as dst:
            dst.write(src.read())


if __name__ == "__main__":
    main(sys.argv[1:])