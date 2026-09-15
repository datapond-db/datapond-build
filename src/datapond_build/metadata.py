"""The datapond data-dictionary convention.

Every database carries two tables the clients read:

``_metadata`` (one row per data table)
    required: table_name, description, row_count, column_count
    recommended: source_url, license, built_at; repos may add columns.

``_columns`` (one row per column of every data table)
    table_name, column_name, data_type, source_file, example_value, join_hint, null_pct

``export_dictionary`` renders both as DICTIONARY.md in either of the two layouts
the existing repos use (``registry`` = eoir/ice/fec/clinicaltrials/cms,
``backtick`` = openpayments/dol-visas).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Literal

import duckdb

DICT_TABLES = ("_metadata", "_columns")


def user_tables(con: duckdb.DuckDBPyConnection) -> list[str]:
    """Base tables in ``main`` other than the dictionary tables, sorted."""
    return [
        r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main' AND table_type = 'BASE TABLE' "
            "AND table_name NOT IN ('_metadata', '_columns') ORDER BY table_name"
        ).fetchall()
    ]


def table_columns(con: duckdb.DuckDBPyConnection, table: str) -> list[str]:
    return [r[0] for r in con.execute(f"DESCRIBE {_q(table)}").fetchall()]


def _q(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


def ensure_metadata(con: duckdb.DuckDBPyConnection, *, descriptions: Mapping[str, str] | None = None,
                    tables: Iterable[str] | None = None, source_url: str | None = None,
                    license: str | None = None, built_at: bool = True, replace: bool = False) -> int:
    """Create or complete the per-table ``_metadata`` table. Returns the row count.

    With ``replace=True`` the table is rebuilt from scratch; otherwise an existing
    table is kept and only missing rows/columns are added (idempotent).
    """
    tables = list(tables) if tables is not None else user_tables(con)
    descriptions = dict(descriptions or {})
    exists = con.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = '_metadata'"
    ).fetchone()[0] > 0
    if replace or not exists:
        con.execute("DROP TABLE IF EXISTS _metadata")
        con.execute(
            "CREATE TABLE _metadata (table_name VARCHAR, description VARCHAR, row_count BIGINT, "
            "column_count INTEGER, source_url VARCHAR, license VARCHAR, built_at TIMESTAMP)"
        )
    have = table_columns(con, "_metadata")
    for col, typ in (("description", "VARCHAR"), ("row_count", "BIGINT"), ("column_count", "INTEGER"),
                     ("source_url", "VARCHAR"), ("license", "VARCHAR"), ("built_at", "TIMESTAMP")):
        if col not in have:
            con.execute(f"ALTER TABLE _metadata ADD COLUMN {col} {typ}")
    now = datetime.now()
    present = {r[0] for r in con.execute("SELECT table_name FROM _metadata").fetchall()}
    for t in tables:
        rc = con.execute(f"SELECT COUNT(*) FROM {_q(t)}").fetchone()[0]
        cc = len(table_columns(con, t))
        if t in present:
            con.execute("UPDATE _metadata SET row_count = ?, column_count = ? WHERE table_name = ?", [rc, cc, t])
            if t in descriptions:
                con.execute("UPDATE _metadata SET description = ? WHERE table_name = ?", [descriptions[t], t])
        else:
            con.execute(
                "INSERT INTO _metadata (table_name, description, row_count, column_count) VALUES (?, ?, ?, ?)",
                [t, descriptions.get(t), rc, cc],
            )
    if source_url:
        con.execute("UPDATE _metadata SET source_url = ?", [source_url])
    if license:
        con.execute("UPDATE _metadata SET license = ?", [license])
    if built_at:
        con.execute("UPDATE _metadata SET built_at = ?", [now])
    return con.execute("SELECT COUNT(*) FROM _metadata").fetchone()[0]


def build_columns_table(con: duckdb.DuckDBPyConnection, *, join_hints: Mapping[str, str] | None = None,
                        tables: Iterable[str] | None = None, example_len: int = 80,
                        quiet: bool = False) -> int:
    """(Re)build ``_columns`` for every data table. Returns the number of rows.

    ``join_hints`` maps a column name to a hint applied wherever that column
    name occurs (the convention all repos use). ``source_file`` is copied from
    ``_metadata`` when that table has such a column. Null rates and example
    values come from one aggregate scan per table; the example is the minimum
    non-null value cast to text, so it is deterministic across rebuilds.
    """
    tables = list(tables) if tables is not None else user_tables(con)
    meta_cols = table_columns(con, "_metadata") if con.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = '_metadata'").fetchone()[0] else []
    con.execute("DROP TABLE IF EXISTS _columns")
    con.execute(
        "CREATE TABLE _columns (table_name VARCHAR, column_name VARCHAR, data_type VARCHAR, "
        "source_file VARCHAR, example_value VARCHAR, join_hint VARCHAR, null_pct DOUBLE)"
    )
    n = 0
    for t in tables:
        cols = con.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = 'main' AND table_name = ? ORDER BY ordinal_position", [t]
        ).fetchall()
        if not cols:
            continue
        source_file = None
        if "source_file" in meta_cols:
            r = con.execute("SELECT source_file FROM _metadata WHERE table_name = ?", [t]).fetchone()
            source_file = r[0] if r else None
        aggs = ["COUNT(*)"]
        for c, _ in cols:
            qc = _q(c)
            aggs.append(f"COUNT(*) FILTER (WHERE {qc} IS NULL)")
            aggs.append(f"MIN(CAST({qc} AS VARCHAR)) FILTER (WHERE {qc} IS NOT NULL)")
        try:
            stats = con.execute(f"SELECT {', '.join(aggs)} FROM {_q(t)}").fetchone()
        except Exception as e:  # noqa: BLE001 - keep going with empty stats
            print(f"  WARNING: column stats for {t}: {e}")
            stats = (0,) + (None, None) * len(cols)
        total = stats[0] or 0
        for i, (c, dt) in enumerate(cols):
            nulls, example = stats[1 + 2 * i], stats[2 + 2 * i]
            null_pct = round(100.0 * nulls / total, 1) if total and nulls is not None else None
            if example is not None:
                example = example.replace("\x00", "")
                if len(example) > example_len:
                    example = example[: example_len - 3] + "..."
            con.execute(
                "INSERT INTO _columns VALUES (?, ?, ?, ?, ?, ?, ?)",
                [t, c, dt, source_file, example, (join_hints or {}).get(c), null_pct],
            )
            n += 1
        if not quiet:
            print(f"  {t}: {len(cols)} columns")
    return n


def export_dictionary(con: duckdb.DuckDBPyConnection, output_path: Path | str, *,
                      title: str = "Data Dictionary", intro: Iterable[str] = (),
                      style: Literal["registry", "backtick"] = "registry",
                      tables: Iterable[str] | None = None) -> Path:
    """Write DICTIONARY.md from ``_columns`` and ``_metadata``.

    ``registry`` style (five original repos)::

        ## table
        <description>
        Source file: `x.csv`
        Rows: 1,234
        | Column | Type | Nulls | Example | Join |

    ``backtick`` style (openpayments, dol-visas)::

        ## table
        <description>. 1,234 rows.
        | Column | Type | Null % | Example |
    """
    output_path = Path(output_path)
    meta_cols = table_columns(con, "_metadata")
    lines = [f"# {title}", ""]
    lines += list(intro)
    if intro:
        lines.append("")
    names = list(tables) if tables is not None else [
        r[0] for r in con.execute("SELECT DISTINCT table_name FROM _columns ORDER BY table_name").fetchall()
    ]
    for t in names:
        sel = ", ".join(c if c in meta_cols else f"NULL AS {c}"
                        for c in ("row_count", "source_file", "description"))
        meta = con.execute(f"SELECT {sel} FROM _metadata WHERE table_name = ?", [t]).fetchone()
        row_count, source_file, description = meta if meta else (None, None, None)
        lines += [f"## {t}", ""]
        if style == "backtick":
            desc = f"{description}. " if description else ""
            lines += [f"{desc}{row_count:,} rows." if row_count is not None else desc.rstrip(), "",
                      "| Column | Type | Null % | Example |", "|--------|------|--------|---------|"]
        else:
            if description:
                lines += [f"{description}", ""]
            if source_file:
                lines.append(f"Source file: `{source_file}`")
            if row_count:
                lines.append(f"Rows: {row_count:,}")
            lines += ["", "| Column | Type | Nulls | Example | Join |", "|--------|------|-------|---------|------|"]
        cols = con.execute(
            "SELECT column_name, data_type, null_pct, example_value, join_hint "
            "FROM _columns WHERE table_name = ? ORDER BY rowid", [t]
        ).fetchall()
        for col, dt, null_pct, example, join_hint in cols:
            if style == "backtick":
                ex = (example or "").replace("|", "/")
                lines.append(f"| `{col}` | {dt} | {null_pct}% | {ex} |")
            else:
                null_str = f"{null_pct:.1f}%" if null_pct is not None else ""
                ex = (example or "").replace("|", "\\|")
                lines.append(f"| {col} | {dt} | {null_str} | {ex} | {join_hint or ''} |")
        lines.append("")
    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  Exported {output_path}")
    return output_path
