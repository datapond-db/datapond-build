"""DuckDB connections tuned for a memory-constrained build machine."""

from __future__ import annotations

import os
from pathlib import Path

import duckdb


def tune(con: duckdb.DuckDBPyConnection, db_path: Path | str, *,
         memory_limit: str = "6GB", threads: int = 4,
         temp_dir: Path | str | None = None) -> None:
    """Apply the standard datapond session settings to an open connection.

    ``DATAPOND_MEMORY_LIMIT`` and ``DATAPOND_THREADS`` override the arguments so
    an operator can dial a build down without editing code. Spills go to
    ``<db>.tmp`` next to the database file unless ``temp_dir`` is given.
    """
    limit = os.environ.get("DATAPOND_MEMORY_LIMIT", memory_limit)
    n_threads = int(os.environ.get("DATAPOND_THREADS", threads))
    tmp = Path(temp_dir) if temp_dir else Path(f"{Path(db_path).resolve()}.tmp")
    con.execute(f"SET memory_limit = '{limit}'")
    con.execute(f"SET threads = {n_threads}")
    con.execute(f"SET temp_directory = '{tmp}'")
    con.execute("SET preserve_insertion_order = false")


def connect(db_path: Path | str, *, fresh: bool = False, memory_limit: str = "6GB",
            threads: int = 4, temp_dir: Path | str | None = None,
            read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Open ``db_path`` with the standard settings.

    ``fresh=True`` deletes an existing file first so a rebuild never inherits
    tables that no longer exist in the new source data.
    """
    db_path = Path(db_path)
    if fresh:
        db_path.unlink(missing_ok=True)
        wal = db_path.with_suffix(db_path.suffix + ".wal")
        wal.unlink(missing_ok=True)
    con = duckdb.connect(str(db_path), read_only=read_only)
    tune(con, db_path, memory_limit=memory_limit, threads=threads, temp_dir=temp_dir)
    return con
