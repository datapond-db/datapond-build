#!/usr/bin/env python3
"""Compare two DICTIONARY.md files ignoring the Example column and row counts.

Usage: python tools/diff_dictionary.py OLD.md NEW.md [--keep-rows]

Exit 0 when the tables, column order, types, null rates and join hints are
identical; otherwise print a unified diff and exit 1. Example values are
non-deterministic across rebuilds and are blanked before comparing.
"""
import argparse
import difflib
import re
import sys
from pathlib import Path


def normalise(text: str, keep_rows: bool) -> list[str]:
    out = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if line.startswith("|") and not line.startswith("|--") and not line.startswith("| Column"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) == 5:      # registry: Column | Type | Nulls | Example | Join
                cells[3] = ""
            elif len(cells) == 4:    # backtick: Column | Type | Null % | Example
                cells[3] = ""
            line = "| " + " | ".join(cells) + " |"
        elif not keep_rows and (line.startswith("Rows:") or re.search(r"\d[\d,]* rows\.$", line)):
            line = re.sub(r"[\d,]+ rows", "N rows", line)
            line = re.sub(r"Rows: [\d,]+", "Rows: N", line)
        out.append(line)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("old"); ap.add_argument("new")
    ap.add_argument("--keep-rows", action="store_true", help="also compare row counts")
    a = ap.parse_args()
    old = normalise(Path(a.old).read_text(encoding="utf-8"), a.keep_rows)
    new = normalise(Path(a.new).read_text(encoding="utf-8"), a.keep_rows)
    diff = list(difflib.unified_diff(old, new, a.old, a.new, lineterm="", n=1))
    if not diff:
        print("dictionaries are equivalent (examples ignored)")
        return 0
    print("\n".join(diff[:200]))
    print(f"\n{sum(1 for d in diff if d.startswith(('+', '-')) and not d.startswith(('+++', '---')))} differing lines")
    return 1


if __name__ == "__main__":
    sys.exit(main())
