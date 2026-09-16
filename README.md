# datapond-build

Shared build tooling for the [datapond](https://github.com/datapond-db) databases.
Each database repo used to carry its own copy of the same five things; this
package holds one version of each.

| Module | Replaces (per repo) |
|--------|---------------------|
| `session.connect()` | `duckdb.connect` + the memory/threads/temp-dir `SET` lines |
| `metadata.ensure_metadata()` / `build_columns_table()` / `export_dictionary()` | `build_metadata`, `build_columns_table`, `export_dictionary` |
| `publish` (+ `card.yaml`) | `publish_to_hf.py` |
| `fetch.download_file()` / `extract_zip()` | the streaming download and unzip helpers |
| `checks.Checker` | the ad-hoc PASS/FAIL printing in `run_validation` |

## Install into a build repo

```bash
uv add "datapond-build @ git+https://github.com/datapond-db/datapond-build@v0.1.2"
```

## Use

```python
from datapond_build import connect, ensure_metadata, build_columns_table, export_dictionary, Checker

con = connect("mydb.duckdb", fresh=True, memory_limit="6GB", threads=4)
# ... load tables ...
ensure_metadata(con, descriptions=TABLE_DESCRIPTIONS, source_url=SOURCE_URL, license="Public domain")
build_columns_table(con, join_hints=JOIN_HINTS)
export_dictionary(con, "DICTIONARY.md", intro=["Source: [Agency](https://...)"], style="registry")

ck = Checker("Audit regression checks")
ck.query(con, "rows present", "SELECT COUNT(*) FROM main_table", lambda n: n > 0)
ck.report(); ck.exit_if_failed()
```

Publish with a `card.yaml` next to the build script:

```bash
python -m datapond_build.publish --db mydb.duckdb --token "$HF_TOKEN" --verify
```

## The dictionary convention

`_metadata`: one row per data table (`table_name`, `description`, `row_count`,
`column_count`, plus `source_url`, `license`, `built_at`). `_columns`: one row per
column (`table_name`, `column_name`, `data_type`, `source_file`, `example_value`,
`join_hint`, `null_pct`). `export_dictionary` renders either the `registry`
layout (`| Column | Type | Nulls | Example | Join |`) or the `backtick` layout
(`| Column | Type | Null % | Example |`).

Environment overrides honoured everywhere: `DATAPOND_MEMORY_LIMIT`, `DATAPOND_THREADS`.

## Migrating a repo

1. Replace the connect + `SET` block with `connect(..., fresh=True)`.
2. Delete the repo's `build_columns_table` / `export_dictionary`; call the shared ones with the
   repo's `join_hints` and `intro` lines. Keep the repo's `build_metadata` if it has extra columns
   (or call `ensure_metadata` after it to fill the standard ones).
3. Replace `publish_to_hf.py` with a `card.yaml`.
4. Rebuild from identical cached inputs and diff `DICTIONARY.md` with the Example column blanked
   (`tools/diff_dictionary.py`); only the `_columns` column order and example values may differ.
