"""Dataset card rendering, HuggingFace upload, and post-publish verification.

The per-repo ``publish_to_hf.py`` scripts differ only in the card text. That
text now lives in a ``card.yaml`` next to the build script::

    repo: Nason/dol-visas-database
    file: dol_visas.duckdb
    pretty_name: DOL H-1B LCA & PERM Labor Certification Database
    license: mit
    tags: [immigration, h1b, perm]
    size_category: 1M<n<10M
    intro: |
      Every H-1B ... as a single queryable DuckDB database.
    quick_start_sql: |
      SELECT ... FROM visas.lca ...
    footer: |
      Build pipeline and documentation: https://github.com/...
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import yaml

from datapond_build.metadata import table_columns


@dataclass
class CardSpec:
    repo: str
    file: str
    pretty_name: str
    intro: str
    license: str = "other"
    tags: list[str] = field(default_factory=list)
    size_category: str = "10M<n<100M"
    quick_start_sql: str = ""
    alias: str = "db"
    footer: str = ""
    title: str | None = None
    table_columns: list[str] = field(default_factory=lambda: ["description", "row_count", "column_count"])


def load_card_spec(path: Path | str = "card.yaml") -> CardSpec:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return CardSpec(**data)


def render_card(spec: CardSpec, db_path: Path | str, *, changelog_path: Path | str | None = None) -> str:
    """Render the HF dataset card (YAML front matter + markdown) for ``db_path``."""
    con = duckdb.connect(str(db_path), read_only=True)
    have = table_columns(con, "_metadata")
    cols = [c for c in spec.table_columns if c in have]
    rows = con.execute(
        f"SELECT table_name, {', '.join(cols)} FROM _metadata ORDER BY row_count DESC NULLS LAST"
    ).fetchall()
    total = con.execute("SELECT SUM(row_count) FROM _metadata").fetchone()[0] or 0
    con.close()

    header = "| Table | " + " | ".join(c.replace("_", " ").title() for c in cols) + " |"
    sep = "|" + "---|" * (len(cols) + 1)
    body = []
    for r in rows:
        cells = []
        for c, v in zip(cols, r[1:]):
            cells.append(f"{v:,}" if isinstance(v, int) and c != "column_count" else ("" if v is None else str(v)))
        body.append(f"| `{r[0]}` | " + " | ".join(cells) + " |")
    attach_url = f"https://huggingface.co/datasets/{spec.repo}/resolve/main/{spec.file}"
    tags = "\n".join(f"  - {t}" for t in spec.tags)
    quick = spec.quick_start_sql.strip() or f"SELECT * FROM {spec.alias}._metadata;"
    card = f"""---
license: {spec.license}
task_categories:
  - tabular-classification
  - tabular-regression
tags:
{tags}
pretty_name: {spec.pretty_name}
size_categories:
  - {spec.size_category}
---

# {spec.title or spec.pretty_name}

{spec.intro.strip()}
**{total:,} rows across {len(rows)} tables.**

{header}
{sep}
{chr(10).join(body)}

## Query it remotely

```sql
INSTALL httpfs; LOAD httpfs;
ATTACH '{attach_url}' AS {spec.alias} (READ_ONLY);

{quick}
```

Or with the datapond packages: `pip install datapond` / `pak::pak("datapond-db/datapond-r")`.

{spec.footer.strip()}
"""
    if changelog_path and Path(changelog_path).exists():
        card += "\n\n" + Path(changelog_path).read_text(encoding="utf-8")
    return card


def publish(db_path: Path | str, spec: CardSpec, *, card_md: str, token: str | None = None,
            card_only: bool = False) -> str:
    """Upload the card (and unless ``card_only`` the database file). Returns the repo URL."""
    from huggingface_hub import HfApi, create_repo

    token = token or os.environ.get("HF_TOKEN")
    api = HfApi(token=token)
    create_repo(spec.repo, repo_type="dataset", exist_ok=True, token=token)
    api.upload_file(path_or_fileobj=card_md.encode("utf-8"), path_in_repo="README.md",
                    repo_id=spec.repo, repo_type="dataset")
    if card_only:
        print("--card-only: skipping .duckdb upload")
    else:
        size_gb = Path(db_path).stat().st_size / 1024**3
        print(f"Uploading {db_path} ({size_gb:.1f} GB) to {spec.repo}/{spec.file} ...")
        api.upload_file(path_or_fileobj=str(db_path), path_in_repo=spec.file,
                        repo_id=spec.repo, repo_type="dataset")
    url = f"https://huggingface.co/datasets/{spec.repo}"
    print(f"Uploaded to {url}")
    return url


def verify_remote(db_path: Path | str, spec: CardSpec, *, retries: int = 5, sleep_s: int = 60) -> bool:
    """True when the published copy's size and ``_metadata`` totals equal the local file's."""
    from huggingface_hub import HfApi

    local_size = Path(db_path).stat().st_size
    lc = duckdb.connect(str(db_path), read_only=True)
    l_meta, l_rows = lc.execute("SELECT COUNT(*), SUM(row_count) FROM _metadata").fetchone()
    lc.close()
    attach_url = f"https://huggingface.co/datasets/{spec.repo}/resolve/main/{spec.file}"
    for attempt in range(1, retries + 1):
        try:
            info = HfApi().get_paths_info(spec.repo, [spec.file], repo_type="dataset")
            remote_size = info[0].size if info else None
            con = duckdb.connect()
            con.execute("SET memory_limit='1GB'")
            con.execute("INSTALL httpfs; LOAD httpfs;")
            con.execute(f"ATTACH '{attach_url}' AS db (READ_ONLY)")
            r_meta, r_rows = con.execute("SELECT COUNT(*), SUM(row_count) FROM db._metadata").fetchone()
            con.close()
            ok = remote_size == local_size and r_meta == l_meta and r_rows == l_rows
            print(f"verify attempt {attempt}: size {remote_size} vs {local_size}; "
                  f"_metadata {r_meta} vs {l_meta}; rows {r_rows} vs {l_rows} -> {'OK' if ok else 'MISMATCH'}")
            if ok:
                return True
        except Exception as e:  # noqa: BLE001
            print(f"verify attempt {attempt}: {str(e)[:160]}")
        if attempt < retries:
            time.sleep(sleep_s)
    return False


def main(argv: list[str] | None = None) -> int:
    """``python -m datapond_build.publish --db x.duckdb [--card card.yaml] [--token ...] [--card-only] [--verify]``"""
    import argparse

    ap = argparse.ArgumentParser(description="Publish a datapond database to Hugging Face")
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--card", default="card.yaml", type=Path)
    ap.add_argument("--changelog", default="CHANGELOG.md", type=Path)
    ap.add_argument("--token")
    ap.add_argument("--card-only", action="store_true")
    ap.add_argument("--verify", action="store_true", help="verify the remote copy after upload")
    ap.add_argument("--render-only", action="store_true", help="print the card and exit")
    a = ap.parse_args(argv)
    if not a.db.exists():
        print(f"Error: {a.db} not found")
        return 1
    spec = load_card_spec(a.card)
    card = render_card(spec, a.db, changelog_path=a.changelog)
    if a.render_only:
        print(card)
        return 0
    publish(a.db, spec, card_md=card, token=a.token, card_only=a.card_only)
    if a.verify and not a.card_only:
        return 0 if verify_remote(a.db, spec) else 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
