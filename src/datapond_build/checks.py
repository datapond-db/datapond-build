"""Collect named PASS/FAIL checks and turn them into an exit status."""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any


class Checker:
    """Accumulates checks; ``failures`` is the count, ``exit_if_failed`` ends the build.

    >>> ck = Checker("Audit regression checks")
    >>> ck.check("rows present", n > 0, f"{n:,} rows")
    >>> ck.report()          # prints the summary line
    >>> ck.exit_if_failed()  # sys.exit(1) when anything failed
    """

    def __init__(self, title: str = "Checks", indent: str = "    "):
        self.title = title
        self.indent = indent
        self.results: list[tuple[str, bool, str]] = []
        print(f"\n  {title}:")

    @property
    def failures(self) -> int:
        return sum(1 for _, ok, _ in self.results if not ok)

    def check(self, name: str, ok: Any, detail: str = "") -> bool:
        ok = bool(ok)
        self.results.append((name, ok, detail))
        print(f"{self.indent}[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
        return ok

    def query(self, con, name: str, sql: str, predicate: Callable[[Any], bool],
              fmt: Callable[[Any], str] = str) -> bool:
        """Run ``sql`` (single value) and check ``predicate(value)``; errors count as failures."""
        try:
            value = con.execute(sql).fetchone()[0]
        except Exception as e:  # noqa: BLE001
            return self.check(name, False, str(e)[:100])
        return self.check(name, predicate(value), fmt(value))

    def report(self) -> int:
        print(f"\n  {self.title}: {self.failures} failure(s)")
        return self.failures

    def exit_if_failed(self, message: str = "do not publish this file") -> None:
        if self.failures:
            print(f"\nBUILD FAILED: {self.failures} check(s) failed -- {message}")
            sys.exit(1)
