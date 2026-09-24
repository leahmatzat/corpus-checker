"""Type A: a static table of what the model trained on (XLSX or CSV).

Which column means what is declared in the registry (`manifestSource.roles`), not
hard-coded here — so a new model with a tabular manifest needs YAML, not code.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import ClassVar, Iterator

from .. import normalize
from . import CanonicalRecord


def read_rows(path: Path) -> Iterator[dict[str, object]]:
    """Rows of the first sheet (XLSX) or of a CSV whose leading `#` lines are provenance."""
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        import openpyxl

        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            rows = wb.worksheets[0].iter_rows(values_only=True)
            header = [str(h).strip() if h is not None else "" for h in next(rows)]
            for values in rows:
                if all(v is None for v in values):
                    continue
                yield dict(zip(header, values))
        finally:
            wb.close()
    else:
        with open(path, newline="", encoding="utf-8") as f:
            lines = (line for line in f if not line.startswith("#"))
            yield from csv.DictReader(lines)


def _cells(value: object) -> int | None:
    s = normalize.text(value)
    try:
        return int(float(s)) if s else None
    except ValueError:
        return None


class TableResolver:
    manifest_type: ClassVar[str] = "A"
    can_prove_presence: ClassVar[bool] = True

    def records(self, source: dict, path: Path) -> Iterator[CanonicalRecord]:
        roles = source.get("roles") or {}
        if not roles:
            raise ValueError(f"manifest source {source['name']!r} declares no column roles")

        def col(row: dict, role: str) -> object:
            return row.get(roles[role]) if role in roles else None

        for i, row in enumerate(read_rows(path), start=1):
            accs: list[str] = []
            for column in roles.get("accession", []):
                accs.extend(normalize.accessions(row.get(column)))
            pmid_cell, doi_cell = col(row, "pmid"), col(row, "doi")
            yield CanonicalRecord(
                accessions=tuple(dict.fromkeys(accs)),
                pmids=normalize.pmids(pmid_cell),
                dois=tuple(dict.fromkeys(normalize.dois(doi_cell) + normalize.dois(pmid_cell))),
                title=normalize.text(col(row, "title")) or None,
                citation=normalize.text(col(row, "citation")) or None,
                project=normalize.text(col(row, "project")) or None,
                sample=normalize.text(col(row, "sample")) or None,
                cells=_cells(col(row, "cells")),
                source=source["name"],
                row=i,
            )


class SupersetTableResolver(TableResolver):
    """Type B: a table of a versioned public corpus the model trained on a SUBSET of.

    Reads exactly like a Type-A table. The difference is the whole rule: absence from
    the superset proves absence from the subset; presence proves nothing.
    """
    manifest_type: ClassVar[str] = "B"
    can_prove_presence: ClassVar[bool] = False
