#!/usr/bin/env python3
"""Count a census release's human cells by dataset, assay, disease, suspension type and primary flag.

A Type-B entry can only say "the model trained on a subset of this release". When a paper
or its code states the rule it used to take that subset, this table is what lets anyone
apply the rule and compare the result with the cell count the paper reports — without
downloading 50M cells' metadata again. Deterministic: rows are sorted, so a re-run for the
same release reproduces the same bytes.

    pip install cellxgene-census      # in a throwaway environment; not a project dependency
    python scripts/census_obs_counts.py 2023-05-15
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import io
from pathlib import Path

COLUMNS = ["dataset_id", "assay", "disease", "suspension_type", "is_primary_data"]


def counts(release: str) -> tuple[str, str]:
    import cellxgene_census

    tally: collections.Counter = collections.Counter()
    with cellxgene_census.open_soma(census_version=release) as census:
        uri = cellxgene_census.get_census_version_description(release)["soma"]["uri"]
        for table in census["census_data"]["homo_sapiens"].obs.read(column_names=COLUMNS):
            df = table.to_pandas()
            for key, n in df.groupby(COLUMNS, observed=True).size().items():
                tally[tuple(str(k) for k in key)] += int(n)

    buf = io.StringIO()
    buf.write(f"# CELLxGENE census {release} — census_data/homo_sapiens/obs cell counts, as served from {uri}\n")
    buf.write(f"# total_cells={sum(tally.values())} rows={len(tally)}\n")
    buf.write("# Regenerate with scripts/census_obs_counts.py; rows sorted so the bytes are reproducible.\n")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(COLUMNS + ["cells"])
    for key in sorted(tally):
        w.writerow(list(key) + [tally[key]])
    return buf.getvalue(), uri


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("release", help="census release, e.g. 2023-05-15")
    p.add_argument("--out", type=Path, help="default: snapshots/cellxgene-census/<release>/human_obs_counts.csv")
    args = p.parse_args()
    out = args.out or Path(__file__).resolve().parents[1] / "snapshots" / "cellxgene-census" / args.release / "human_obs_counts.csv"
    text, uri = counts(args.release)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"{out}\n  sha256 {hashlib.sha256(text.encode('utf-8')).hexdigest()}\n  bytes  {len(text.encode('utf-8'))}\n  source {uri}")


if __name__ == "__main__":
    main()
