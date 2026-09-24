"""corpus-checker command line.

EXIT CODES DO NOT ENCODE VERDICTS. A PRESENT is not a failure and a NOT PRESENT is
not a success — the tool reports exposure, not effect.

    0  ran; validate found no errors (warnings allowed)
    1  tool error (network, git, unreadable file)
    2  validate found a violation
    3  bad usage
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .crosswalk import CrosswalkError, as_dict, resolve
from .registry import repo_root
from .validate import ERROR, WARNING, Options, validate

EXIT_OK, EXIT_TOOL, EXIT_VIOLATION, EXIT_USAGE = 0, 1, 2, 3


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:   # argparse would exit 2, which means "violation" here
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog}: error: {message}\n")


def _build_parser() -> argparse.ArgumentParser:
    p = _Parser(prog="corpus-checker", description="Is a model's evaluation data inside its pretraining corpus?")
    p.add_argument("--format", choices=["table", "json"], default="table")
    p.add_argument("--root", type=Path, help="repository root (default: found from the current directory)")
    sub = p.add_subparsers(dest="command", required=True, parser_class=_Parser)

    v = sub.add_parser("validate", help="check registry/ and datasets/ against the schema and guards G00–G20")
    v.add_argument("paths", nargs="*", type=Path, help="files to check (default: all of registry/ and datasets/)")
    v.add_argument("--base-ref", help="git ref to diff against for G03 (stale verification). CI passes the PR base.")
    v.add_argument("--allow-draft", action="store_true", help="report an empty verified_by as a warning, not an error")
    v.add_argument("--check-sources", action="store_true", help="re-fetch manifest sources and compare sha256 (G04; network)")
    v.add_argument("--stale-months", type=int, default=12, help="warn when a manifest's as_of is older than this (G05)")

    c = sub.add_parser("check", help="show a model's findings; with --manifests, re-run them from the manifest files")
    c.add_argument("model", help="registry id, e.g. scfoundation")
    c.add_argument("--manifests", type=Path,
                   help="directory holding the manifest files (matched by URL file name and sha256). "
                        "Re-runs every catalog dataset and compares with the registry.")
    c.add_argument("--rerun", action="store_true",
                   help="re-run from committed snapshots/extracts (or --manifests), instead of showing recorded findings")
    c.add_argument("--dataset", action="append", help="limit to these datasets/ ids (repeatable)")

    lg = sub.add_parser("ledger", help="every dataset × model: who trained on it, who evaluated on it")
    lg.add_argument("--dataset", action="append", help="limit to these datasets/ ids (repeatable)")

    r = sub.add_parser("resolve", help="crosswalk lookup: GSE…/GSM…/PMID/DOI -> canonical triple")
    r.add_argument("identifier", help="a GEO accession (GSE…/GSM…), a PMID, or a DOI (10.…)")
    r.add_argument("--no-network", action="store_true", help="cache only; a cache miss is a tool error")

    bs = sub.add_parser("build-site", help="generate the static site (matrix, model/dataset pages, about) from the ledger")
    bs.add_argument("--out", type=Path, required=True, help="output directory, e.g. site/_build")
    bs.add_argument("--repo-url", help="GitHub repo URL for correction/source links "
                                        "(default: $CORPUS_CHECKER_REPO_URL, else a visible placeholder)")
    return p


def _print_table(issues, n_files: int) -> None:
    by_path: dict[str, list] = {}
    for issue in issues:
        by_path.setdefault(issue.path, []).append(issue)
    for path, items in sorted(by_path.items()):
        print(path)
        for i in sorted(items, key=lambda i: (i.severity != ERROR, i.code, i.where)):
            print(f"  {i.severity.upper():7} {i.code}  {i.where}")
            print(f"                {i.message}")
    errors = sum(i.severity == ERROR for i in issues)
    warnings = sum(i.severity == WARNING for i in issues)
    print(f"\n{n_files} file(s) checked · {errors} error(s) · {warnings} warning(s)")


def _cmd_validate(args, root: Path) -> int:
    paths = [p.resolve() for p in args.paths] or None
    opts = Options(allow_draft=args.allow_draft, stale_months=args.stale_months,
                   base_ref=args.base_ref, check_sources=args.check_sources)
    try:
        issues = validate(root, paths, opts)
    except (OSError, RuntimeError) as exc:
        print(f"corpus-checker: {exc}", file=sys.stderr)
        return EXIT_TOOL
    n_files = len(paths) if paths else len(list((root / "registry").glob("*.yaml"))) + len(list((root / "datasets").glob("*.yaml")))
    if args.format == "json":
        print(json.dumps([i.as_dict() for i in issues], indent=2))
    else:
        _print_table(issues, n_files)
    return EXIT_VIOLATION if any(i.severity == ERROR for i in issues) else EXIT_OK


_EXPOSURE_NOTE = ("Exposure, not effect: overlap does not by itself mean a reported number is inflated. "
                  "NOT CHECKABLE is a finding about what was published, not an error.")


def _evidence(verdict: str, samples: int | None, cells: int | None, reason: str | None) -> str:
    if verdict == "PRESENT":
        parts = [f"{samples} samples" if samples else None, f"{cells:,} cells" if cells is not None else None]
        return " · ".join(p for p in parts if p)
    return reason or ""


def _cmd_check(args, root: Path) -> int:
    from .check import SourceError, check_corpus, compare, locate_in_dir, locate_committed
    from .registry import load_catalog, load_yaml, relation

    path = root / "registry" / f"{args.model}.yaml"
    if not path.is_file():
        known = ", ".join(p.stem for p in sorted((root / "registry").glob("*.yaml")))
        print(f"corpus-checker: no registry entry {args.model!r} (known: {known})", file=sys.stderr)
        return EXIT_USAGE
    entry, catalog = load_yaml(path), load_catalog(root)
    if unknown := [d for d in (args.dataset or []) if d not in catalog]:
        print(f"corpus-checker: not in datasets/: {', '.join(unknown)}", file=sys.stderr)
        return EXIT_USAGE

    rows, sections = [], []
    for corpus in entry["corpora"]:
        m = corpus["manifest"]
        header = (f"{entry['model']} · {corpus['stage']} · manifest type {m['type']}"
                  + (f" ({m['identifier_type']}-keyed)" if m.get("identifier_type") else ""))
        if args.manifests or args.rerun:
            locate = locate_committed(root, locate_in_dir(args.manifests) if args.manifests else None)
            try:
                run = check_corpus(entry, corpus, catalog, locate, args.dataset)
            except SourceError as exc:
                print(f"corpus-checker: {exc}", file=sys.stderr)
                return EXIT_TOOL
            recorded = {f["dataset"]: f for f in corpus["result"]["findings"]}
            diffs = {(d.dataset, d.field) for d in compare(corpus, run)}
            section = []
            for r in run.results:
                agree = ("not in registry" if r.dataset not in recorded
                         else "differs from registry" if any(ds == r.dataset for ds, _ in diffs) else "= registry")
                section.append({"stage": corpus["stage"], "relation": r.relation, "dataset": r.dataset,
                                "verdict": r.verdict, "keys_attempted": list(r.keys_attempted),
                                "matched_on": list(r.matched_on), "samples": len(r.sample_ids) or None,
                                "cells": r.cells, "reason": r.decision.reason, "registry": agree,
                                "registry_verdict": recorded.get(r.dataset, {}).get("verdict")})
            origin = f"re-run from {args.manifests}" if args.manifests else "re-run from committed snapshots and extracts"
            sections.append((header, f"{origin} · searched: {', '.join(run.sources_searched)}", section))
        else:
            section = []
            for f in corpus["result"]["findings"]:
                if args.dataset and f["dataset"] not in args.dataset:
                    continue
                section.append({"stage": corpus["stage"], "relation": relation(entry, f["dataset"]),
                                "dataset": f["dataset"], "verdict": f["verdict"],
                                "keys_attempted": f.get("keys_attempted", []), "matched_on": f.get("matched_on", []),
                                "samples": f.get("samples"), "cells": f.get("cells"), "reason": f.get("reason")})
            prov = entry["provenance"]
            signed = f"verified by {prov['verified_by']} {prov['verified_date']}" if prov["verified_by"] else "DRAFT — not verified"
            sections.append((header, f"as recorded in registry/{args.model}.yaml · {signed} · "
                                     f"searched: {', '.join(corpus['result']['sources_searched']) or '(nothing to search)'}", section))
        rows += section

    if args.format == "json":
        print(json.dumps(rows, indent=2))
        return EXIT_OK

    order = {"self-eval": 0, "catalog": 1}
    for header, sub, section in sections:
        print(header)
        print(f"  {sub}\n")
        print(f"  {'RELATION':10} {'DATASET':20} {'VERDICT':14} {'KEYS TRIED':16} EVIDENCE")
        for r in sorted(section, key=lambda r: (order[r["relation"]], r["dataset"])):
            evidence = _evidence(r["verdict"], r["samples"], r["cells"], r["reason"])
            if "registry" in r and r["registry"] != "= registry":
                evidence = f"[{r['registry']}{': ' + r['registry_verdict'] if r.get('registry_verdict') else ''}] {evidence}"
            if len(evidence) > 110:
                evidence = evidence[:109] + "…   (--format json for the full reason)"
            print(f"  {r['relation']:10} {r['dataset']:20} {r['verdict']:14} {','.join(r['keys_attempted']) or '-':16} {evidence}")
        print()
    print(_EXPOSURE_NOTE)
    return EXIT_OK


def _cmd_ledger(args, root: Path) -> int:
    from .ledger import build_ledger

    ledger = build_ledger(root)
    wanted = args.dataset or sorted(ledger["datasets"])
    if unknown := [d for d in wanted if d not in ledger["datasets"]]:
        print(f"corpus-checker: not in datasets/: {', '.join(unknown)}", file=sys.stderr)
        return EXIT_USAGE
    if args.format == "json":
        if args.dataset:
            ledger = {**ledger, "datasets": {d: ledger["datasets"][d] for d in wanted},
                      "rows": [r for r in ledger["rows"] if r["dataset"] in wanted]}
        print(json.dumps(ledger, indent=2, ensure_ascii=False))
        return EXIT_OK

    models = ledger["models"]
    for d_id in wanted:
        d = ledger["datasets"][d_id]
        print(f"{d['name']}  ({d_id})")
        print(f"  evaluated by: {', '.join(d['evaluated_by']) or '—'}")
        for r in (r for r in ledger["rows"] if r["dataset"] == d_id):
            draft = "  [draft]" if models[r["model"]]["draft"] else ""
            print(f"  {r['model']:18} {r['stage']:14} {r['relation']:10} {r['verdict']}{draft}")
        for x in d["reuse"]:
            print(f"  ★ in {x['in_training_of']}'s {x['stage']} data, and {x['evaluated_by']} evaluates on it")
        print()
    print(_EXPOSURE_NOTE)
    return EXIT_OK


def _print_record_table(record) -> None:
    print(f"query      {record.query}")
    print(f"accessions {', '.join(record.accessions) or '(none)'}")
    print(f"pmids      {', '.join(record.pmids) or '(none)'}")
    print(f"doi        {record.doi or '(none)'}")
    print(f"pmcid      {record.pmcid or '(none)'}")
    print(f"title      {record.title or '(none)'}")
    print(f"organism   {record.organism or '(none)'}")
    print(f"samples    {len(record.samples)}")
    for acc, title in record.samples[:5]:
        print(f"             {acc}  {title}")
    if len(record.samples) > 5:
        print(f"             ... +{len(record.samples) - 5} more")
    print(f"retrieved  {record.retrieved}")
    print(f"sources    {', '.join(record.sources) or '(none)'}")


def _cmd_resolve(args, root: Path) -> int:
    try:
        record = resolve(args.identifier, network=not args.no_network, cache_dir=root / "crosswalk" / "cache")
    except CrosswalkError as exc:
        print(f"corpus-checker: {exc}", file=sys.stderr)
        return EXIT_TOOL
    if args.format == "json":
        print(json.dumps(as_dict(record), indent=2, sort_keys=True))
    else:
        _print_record_table(record)
    return EXIT_OK


def _cmd_build_site(args, root: Path) -> int:
    from .site import PLACEHOLDER_REPO_URL, build_site

    repo_url = args.repo_url or os.environ.get("CORPUS_CHECKER_REPO_URL") or PLACEHOLDER_REPO_URL
    try:
        files = build_site(root, args.out, repo_url)
    except (OSError, RuntimeError) as exc:
        print(f"corpus-checker: {exc}", file=sys.stderr)
        return EXIT_TOOL
    print(f"corpus-checker: wrote {len(files)} file(s) to {args.out}")
    if repo_url == PLACEHOLDER_REPO_URL:
        print(f"corpus-checker: no --repo-url given — correction/source links use the "
              f"placeholder {PLACEHOLDER_REPO_URL}", file=sys.stderr)
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    root = (args.root or repo_root()).resolve()
    if args.command == "validate":
        return _cmd_validate(args, root)
    if args.command == "check":
        return _cmd_check(args, root)
    if args.command == "ledger":
        return _cmd_ledger(args, root)
    if args.command == "resolve":
        return _cmd_resolve(args, root)
    if args.command == "build-site":
        return _cmd_build_site(args, root)
    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
