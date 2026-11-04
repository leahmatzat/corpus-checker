#!/usr/bin/env python3
"""Write a committed extract of a publisher's manifest file: chosen columns, every row, values unchanged.

Publisher files are not committed (their licences vary), so a re-run reads an extract
instead: the identifier columns of every row, under a header that declares the
publisher file's sha256 (G22). Regenerating from the same file reproduces the same bytes.

    python scripts/make_extract.py MOESM4_ESM.xlsx --sheet "Sheet 1 - full_train_datasets" \\
        --header-row 2 --columns "Dataset (CxG ID)" num_cells --describe "Supplementary Table 16, UCE (…)" \\
        --out extracts/uce/table16.csv
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
from pathlib import Path


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def read_table(path: Path, sheet: str | None, header_row: int) -> tuple[list[str], list[list[str]]]:
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        import openpyxl

        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            ws = wb[sheet] if sheet else wb.worksheets[0]
            rows = [[_cell(v) for v in r] for r in ws.iter_rows(values_only=True)]
        finally:
            wb.close()
    else:
        with open(path, newline="", encoding="utf-8") as f:
            rows = [[_cell(v) for v in r] for r in csv.reader(f)]
    header, body = rows[header_row - 1], rows[header_row:]
    return header, [r for r in body if any(r)]


def extract(path: Path, *, sheet: str | None, header_row: int, columns: list[str], describe: str) -> str:
    header, body = read_table(path, sheet, header_row)
    missing = [c for c in columns if c not in header]
    if missing:
        raise SystemExit(f"columns not in the header: {missing} (header: {header})")
    idx = [header.index(c) for c in columns]
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    buf = io.StringIO()
    buf.write(f"# Derived extract of {describe}.\n")
    buf.write(f"# derived-from-sha256: {digest}\n")
    where = f"sheet {sheet!r}, " if sheet else ""
    buf.write(f"# Columns kept: {', '.join(columns)} — {where}every row, values unchanged. "
              "The publisher file itself is not committed.\n")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(columns)
    for r in body:
        w.writerow([r[i] if i < len(r) else "" for i in idx])
    return buf.getvalue()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("file", type=Path, help="the publisher's file, as downloaded")
    p.add_argument("--sheet", help="worksheet name (xlsx); default: the first")
    p.add_argument("--header-row", type=int, default=1, help="1-based row holding the column names")
    p.add_argument("--columns", nargs="+", required=True)
    p.add_argument("--describe", required=True, help='what the file is, e.g. "Supplementary Data 2, scFoundation (…)"')
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    text = extract(args.file, sheet=args.sheet, header_row=args.header_row, columns=args.columns, describe=args.describe)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    rows = text.count("\n") - 4
    print(f"{args.out}\n  rows   {rows}\n  sha256 {hashlib.sha256(text.encode('utf-8')).hexdigest()}")


if __name__ == "__main__":
    main()
