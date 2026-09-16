"""datapond-build: the pieces every datapond database build repeats.

- session:  a DuckDB connection tuned for a small build machine
- fetch:    streaming downloads and zip extraction with retries
- metadata: per-table ``_metadata``, the ``_columns`` dictionary, DICTIONARY.md
- publish:  dataset card rendering, HuggingFace upload, remote verification
- checks:   collect PASS/FAIL checks and turn them into an exit code
"""

__version__ = "0.1.4"

from datapond_build.checks import Checker
from datapond_build.metadata import build_columns_table, ensure_metadata, export_dictionary, user_tables
from datapond_build.session import connect

__all__ = ["Checker", "build_columns_table", "connect", "ensure_metadata", "export_dictionary", "user_tables"]
