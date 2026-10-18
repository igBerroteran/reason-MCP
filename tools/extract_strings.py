"""Extract unique printable strings (ASCII and UTF-16LE) from a binary, one per line.

Used to validate remotable item names: python tools/extract_strings.py Reason.exe strings.txt
"""

import re
import sys


def main():
    src, dst = sys.argv[1], sys.argv[2]
    data = open(src, "rb").read()
    found = set()
    for m in re.finditer(rb"[\x20-\x7e]{3,}", data):
        found.add(m.group().decode("ascii"))
    for m in re.finditer(rb"(?:[\x20-\x7e]\x00){3,}", data):
        found.add(m.group().decode("utf-16le"))
    with open(dst, "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(found)))
    print(len(found), "strings ->", dst)


if __name__ == "__main__":
    main()
