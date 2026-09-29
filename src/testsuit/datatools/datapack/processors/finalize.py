#!/usr/bin/env python3

import os
import sys
import stat
import datetime

script_name = os.path.basename(__file__)
script_dir = os.path.dirname(os.path.abspath(__file__))


def main(args):
    if len(args) < 1:
        print("Usage: {} <DATASET_DIR>".format(script_name), file=sys.stderr)
        sys.exit(1)

    DATASET_DIR = args[0]

    targetdir = os.path.realpath(DATASET_DIR)

    dataset_name = os.path.basename(os.path.dirname(targetdir))

    now = datetime.datetime.now()
    import_date = now.strftime("%Y-%m-%d %H:%m:%S")
    import_date_sec = str(int(now.timestamp()))

    dico_path = os.path.join(DATASET_DIR, "dataset.dico")
    with open(dico_path, "a") as f:
        f.write("{}.importdate={}\n".format(dataset_name, import_date))
        f.write("{}.importdateSec={}\n".format(dataset_name, import_date_sec))

    # chmod -R 550 (r-xr-x---)
    for root, dirs, files in os.walk(DATASET_DIR):
        os.chmod(root, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH)
        for name in dirs + files:
            path = os.path.join(root, name)
            if not os.path.islink(path):
                os.chmod(path, stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP)


if __name__ == "__main__":
    main(sys.argv[1:])