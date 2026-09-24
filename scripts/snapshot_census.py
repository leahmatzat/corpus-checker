#!/usr/bin/env python3
"""Snapshot a CELLxGENE census release's dataset table into a committed CSV.

Type-B manifests point at a versioned public corpus. Querying it live needs
cellxgene-census + tiledbsoma (heavy) and depends on the release staying online, so
the dataset table is snapshotted once and committed; the check then runs offline and
the snapshot's sha256 is recorded in the registry.

The output is deterministic (rows sorted by dataset_id, fixed columns), so re-running
this for the same release must reproduce the same bytes and the same sha256 — that is
how a snapshot is re-verified.

    pip install cellxgene-census      # in a throwaway environment; not a project dependency
    python scripts/snapshot_census.py 2023-05-15
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
from pathlib import Path

COLUMNS = ["dataset_id", "collection_id", "collection_name", "collection_doi", "dataset_title", "dataset_total_cell_count"]


def snapshot(release: str) -> tuple[str, dict]:
    import cellxgene_census

    with cellxgene_census.open_soma(census_version=release) as census:
        datasets = census["census_info"]["datasets"].read().concat().to_pandas()
        summary = census["census_info"]["summary"].read().concat().to_pandas()
        uri = cellxgene_census.get_census_version_description(release)["soma"]["uri"]
    summary = dict(zip(summary["label"], summary["value"]))

    rows = datasets[COLUMNS].fillna("").sort_values("dataset_id")
    total = int(rows["dataset_total_cell_count"].sum())
    if str(total) != str(summary.get("total_cell_count")):
        raise SystemExit(f"dataset cell counts sum to {total}, census summary says {summary.get('total_cell_count')}")

    buf = io.StringIO()
    buf.write(f"# CELLxGENE census {release} — census_info/datasets, as served from {uri}\n")
    buf.write(f"# census_build_date={summary.get('census_build_date')} total_cell_count={summary.get('total_cell_count')} "
              f"unique_cell_count={summary.get('unique_cell_count')} datasets={len(rows)}\n")
    buf.write("# Regenerate with scripts/snapshot_census.py; rows sorted by dataset_id so the bytes are reproducible.\n")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(COLUMNS)
    for rec in rows.itertuples(index=False):
        w.writerow(list(rec))
    return buf.getvalue(), {"uri": uri, **summary}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("release", help="census release, e.g. 2023-05-15")
    p.add_argument("--out", type=Path, help="default: snapshots/cellxgene-census/<release>/datasets.csv")
    args = p.parse_args()
    out = args.out or Path(__file__).resolve().parents[1] / "snapshots" / "cellxgene-census" / args.release / "datasets.csv"
    text, meta = snapshot(args.release)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    print(f"{out}\n  sha256 {digest}\n  bytes  {len(text.encode('utf-8'))}\n  source {meta['uri']}\n  taken  {dt.date.today()}")


if __name__ == "__main__":
    main()
