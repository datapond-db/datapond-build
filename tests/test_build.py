import os
from pathlib import Path

import duckdb
import pytest

from datapond_build import Checker, build_columns_table, connect, ensure_metadata, export_dictionary
from datapond_build.publish import CardSpec, render_card


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "fixture.duckdb"
    con = connect(path, fresh=True, memory_limit="256MB", threads=1)
    con.execute("CREATE TABLE cases (IDNCASE INTEGER, NAT VARCHAR, filed DATE)")
    con.execute("INSERT INTO cases VALUES (1,'MX','2020-01-01'),(2,'GT',NULL),(3,NULL,'2021-05-05')")
    con.execute("CREATE TABLE lu_nationality (NAT_CODE VARCHAR, NAT_NAME VARCHAR)")
    con.execute("INSERT INTO lu_nationality VALUES ('MX','Mexico'),('GT','Guatemala')")
    yield con, path
    con.close()


def test_connect_applies_settings_and_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("DATAPOND_MEMORY_LIMIT", "300MB")
    monkeypatch.setenv("DATAPOND_THREADS", "1")
    con = connect(tmp_path / "x.duckdb", fresh=True)
    limit = con.execute("SELECT current_setting('memory_limit')").fetchone()[0]
    threads = con.execute("SELECT current_setting('threads')").fetchone()[0]
    assert limit.replace(" ", "").upper().startswith("28") or "MB" in limit  # duckdb normalises units
    assert int(threads) == 1
    assert con.execute("SELECT current_setting('preserve_insertion_order')").fetchone()[0] in (False, "false")
    con.close()


def test_fresh_removes_existing_file(tmp_path):
    path = tmp_path / "x.duckdb"
    con = connect(path, fresh=True); con.execute("CREATE TABLE t (a INT)"); con.close()
    con = connect(path, fresh=True)
    assert con.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_name='t'").fetchone()[0] == 0
    con.close()


def test_metadata_and_columns(db):
    con, path = db
    n = ensure_metadata(con, descriptions={"cases": "Court cases"}, source_url="https://x", license="Public domain")
    assert n == 2
    rows = dict(con.execute("SELECT table_name, row_count FROM _metadata").fetchall())
    assert rows == {"cases": 3, "lu_nationality": 2}
    assert con.execute("SELECT description FROM _metadata WHERE table_name='cases'").fetchone()[0] == "Court cases"
    # idempotent: re-running keeps descriptions and refreshes counts
    con.execute("INSERT INTO cases VALUES (4,'HN',NULL)")
    ensure_metadata(con)
    assert con.execute("SELECT row_count, description FROM _metadata WHERE table_name='cases'").fetchone() == (4, "Court cases")

    n = build_columns_table(con, join_hints={"NAT": "Joins to lu_nationality.NAT_CODE"}, quiet=True)
    assert n == 5
    cols = con.execute("DESCRIBE _columns").fetchall()
    assert [c[0] for c in cols] == ["table_name", "column_name", "data_type", "source_file", "example_value", "join_hint", "null_pct"]
    nat = con.execute("SELECT example_value, join_hint, null_pct FROM _columns WHERE column_name='NAT'").fetchone()
    assert nat == ("GT", "Joins to lu_nationality.NAT_CODE", 25.0)  # MIN of non-null values, 1 of 4 null


def test_export_both_styles(db, tmp_path):
    con, path = db
    ensure_metadata(con, descriptions={"cases": "Court cases"})
    build_columns_table(con, quiet=True)
    reg = export_dictionary(con, tmp_path / "reg.md", intro=["Source: [X](https://x)"], style="registry")
    txt = reg.read_text()
    assert "# Data Dictionary\n\nSource: [X](https://x)\n\n## cases\n\nCourt cases\n\nRows: 3\n\n| Column | Type | Nulls | Example | Join |" in txt
    assert "| NAT | VARCHAR | 33.3% | GT |  |" in txt
    bt = export_dictionary(con, tmp_path / "bt.md", title="x Data Dictionary", style="backtick")
    txt = bt.read_text()
    assert "## cases\n\nCourt cases. 3 rows.\n\n| Column | Type | Null % | Example |" in txt
    assert "| `NAT` | VARCHAR | 33.3% | GT |" in txt


def test_checker_counts_failures(capsys):
    ck = Checker("T")
    assert ck.check("ok", True)
    assert not ck.check("bad", 0, "zero")
    con = duckdb.connect()
    ck.query(con, "q", "SELECT 5", lambda v: v > 3)
    ck.query(con, "err", "SELECT * FROM nope", lambda v: True)
    assert ck.report() == 2
    with pytest.raises(SystemExit):
        ck.exit_if_failed()


def test_render_card(db, tmp_path):
    con, path = db
    ensure_metadata(con, descriptions={"cases": "Court cases"})
    con.close()
    spec = CardSpec(repo="Nason/x-database", file="x.duckdb", pretty_name="X DB", intro="Intro.",
                    tags=["a"], quick_start_sql="SELECT 1;", alias="x", footer="Footer.")
    cl = tmp_path / "CHANGELOG.md"; cl.write_text("# Changelog\n\n## v1\n")
    card = render_card(spec, path, changelog_path=cl)
    assert card.startswith("---\nlicense: other")
    assert "**5 rows across 2 tables.**" in card
    assert "| `cases` | Court cases | 3 | 3 |" in card
    assert "ATTACH 'https://huggingface.co/datasets/Nason/x-database/resolve/main/x.duckdb' AS x (READ_ONLY);" in card
    assert card.rstrip().endswith("## v1")
