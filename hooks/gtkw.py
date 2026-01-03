#!/usr/bin/env python3

from __future__ import annotations

import os, re, argparse
from typing import Sequence

FILEPATH_RE = re.compile(r'(^\[(?:dumpfile|savefile)\]\s+)"(.+?\.[a-z]+)"')

def fix_file(filename: str) -> int:
    """ When you save a GTKWave Save File it outputs absolute filepaths to the
    dumpfile and savefile.  For portability, strip it.

    """
    with open(filename) as f:
        contents = f.readlines()

    new_contents = []
    for line in contents:
        match = FILEPATH_RE.match(line)
        if match is not None:
            line = match.group(1) + '"' + os.path.basename(match.group(2)) + '"\n'
        new_contents.append(line)

    if contents != new_contents:
        with open(filename, 'w') as f:
            f.write(''.join(new_contents))
        return 1
    else:
        return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('filenames', nargs='*', help='XML filenames to check.')
    args = parser.parse_args(argv)

    retval = 0
    for filename in args.filenames:
        retv = fix_file(filename)
        if retv:
            print(f"Updated {filename}")
        retval |= retv

    return retval


if __name__ == '__main__':
    raise SystemExit(main())
