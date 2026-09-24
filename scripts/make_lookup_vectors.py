#!/usr/bin/env python3
"""Generate tests/fixtures/lookup_vectors.json from the Python engine.

The vectors are the contract for any other implementation of the lookup rules — the
site's lookup.js is tested against them. Regenerate after changing normalize, match or
verdict rules; tests/test_lookup.py fails if the committed file is stale.

    python scripts/make_lookup_vectors.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from corpus_checker.ledger import build_ledger  # noqa: E402
from corpus_checker.lookup import Query, accession_type, load_corpora, parse, search  # noqa: E402
from corpus_checker.verdict import decide_from  # noqa: E402

OUT = ROOT / "tests" / "fixtures" / "lookup_vectors.json"

PARSE_INPUTS = [
    "GSE133344", "GEO-GSE133344", "  gse133344 ", "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE133344",
    "GSM3906020", "E-MTAB-5061", "AE-E-MTAB-11536", "SRP073767", "PRJNA318252", "EGAS00001004107",
    "31395745", "PMID: 31395745", "https://pubmed.ncbi.nlm.nih.gov/31395745/",
    "10.1126/science.aax4438", "https://doi.org/10.1126/science.aax4438", "doi:10.1016/j.jhep.2020.05.039",
    "283d65eb-dd53-496d-adb7-7570c7caa443", "283D65EB-DD53-496D-ADB7-7570C7CAA443",
    "GSE9054", "Norman 2019", "hello",
]

ACCESSION_TYPE_INPUTS = ["GSE133344", "GSM3906020", "E-MTAB-5061", "PRJNA318252", "SRP073767", "SRX1723926",
                         "EGAS00001004107", "SCP1470", "HRA000150", "283d65eb-dd53-496d-adb7-7570c7caa443"]

DECIDE_CASES = [
    ("no manifest", dict(manifest_type="C-vague", can_prove_presence=False)),
    ("not checkable flag", dict(manifest_type="A", can_prove_presence=True, checkable=False)),
    ("unconfirmed source, hit", dict(manifest_type="A", can_prove_presence=True, keys_attempted=["accession"],
                                     keys_hit=["accession"], unconfirmed_sources=["S2"])),
    ("unconfirmed source, no hit", dict(manifest_type="A", can_prove_presence=True, keys_attempted=["accession"],
                                        unconfirmed_sources=["S2"])),
    ("accession hit", dict(manifest_type="A", can_prove_presence=True, keys_attempted=["accession", "pmid"],
                           keys_hit=["accession", "pmid"], requires_keys=["accession", "pmid"])),
    ("superset, accession hit", dict(manifest_type="B", can_prove_presence=False, keys_attempted=["accession", "doi"],
                                     keys_hit=["accession", "doi"])),
    ("superset, paper-only hit", dict(manifest_type="B", can_prove_presence=False, keys_attempted=["doi"],
                                      keys_hit=["doi"])),
    ("same publication, accession listed", dict(manifest_type="A", can_prove_presence=True,
                                                keys_attempted=["accession", "pmid"], keys_hit=["pmid"],
                                                listed_accessions=["GSE2"], requires_keys=["accession", "pmid"])),
    ("same publication, nothing listed", dict(manifest_type="A", can_prove_presence=True, keys_attempted=["pmid", "doi"],
                                              keys_hit=["pmid", "doi"])),
    ("weak only", dict(manifest_type="A", can_prove_presence=True, keys_attempted=["title"], keys_hit=["title"])),
    ("unsearched source", dict(manifest_type="A", can_prove_presence=True, keys_attempted=["accession"],
                               unsearched_sources=["S2"])),
    ("no exact key", dict(manifest_type="A", can_prove_presence=True, keys_attempted=[])),
    ("missing required key", dict(manifest_type="A", can_prove_presence=True, keys_attempted=["pmid"],
                                  requires_keys=["accession", "pmid"])),
    ("absent", dict(manifest_type="A", can_prove_presence=True, keys_attempted=["accession", "pmid"],
                    requires_keys=["accession", "pmid"])),
    ("absent from superset", dict(manifest_type="B", can_prove_presence=False, keys_attempted=["doi"],
                                  requires_keys=["doi"])),
]

# Pre-expanded queries (no network): what the browser has after its NCBI step.
LOOKUP_CASES = [
    ("Norman, catalog dataset", "dataset", "norman2019",
     [["geo", "GSE133344"], ["pmid", "31395745"], ["doi", "10.1126/science.aax4438"]]),
    ("Norman accession only, not via catalog", "dataset", None, [["geo", "GSE133344"]]),
    ("Norman, one sample", "dataset", None, [["geo", "GSM3906020"], ["pmid", "31395745"]]),
    ("Norman paper only", "paper", None, [["pmid", "31395745"], ["doi", "10.1126/science.aax4438"]]),
    ("Zheng mouse brain", "dataset", None,
     [["geo", "GSE93421"], ["bioproject", "PRJNA360949"], ["sra", "SRP096558"], ["pmid", "28091601"],
      ["doi", "10.1038/ncomms14049"]]),
    ("unknown series with a paper", "dataset", None, [["geo", "GSE1"], ["pmid", "1"]]),
    ("HBCA collection UUID", "dataset", None, [["cellxgene_dataset", "283d65eb-dd53-496d-adb7-7570c7caa443"]]),
    ("Zheng68K, provisional SRA accession, computed", "dataset", None,
     [["sra", "SRX1723926", "provisional", "SRA title and submitter only"], ["sra", "SRR3561754", "provisional",
      "SRA title and submitter only"], ["pmid", "28091601"], ["doi", "10.1038/ncomms14049"]]),
    ("Zheng68K, catalog dataset", "dataset", "zheng2017-pbmc68k",
     [["pmid", "28091601"], ["doi", "10.1038/ncomms14049"],
      ["sra", "SRX1723926", "provisional", "SRA title and submitter only"],
      ["sra", "SRR3561754", "provisional", "SRA title and submitter only"]]),
    ("myeloid, catalog dataset", "dataset", "cheng2021-myeloid",
     [["geo", "GSE154763"], ["pmid", "33545035"], ["doi", "10.1016/j.cell.2021.01.010"]]),
]


def _answers(result) -> list[dict]:
    return [{"model": a.model, "stage": a.stage, "verdict": a.verdict, "code": a.code, "match_level": a.match_level,
             "keys_attempted": list(a.keys_attempted), "matched_on": list(a.matched_on),
             "samples": a.samples, "cells": a.cells, "recorded": a.recorded, "identity": a.identity}
            for a in result.answers]


def build() -> dict:
    vectors = {
        "comment": "Generated by scripts/make_lookup_vectors.py from the Python engine. Do not edit by hand. "
               "Identifiers are [type, value] or [type, value, 'provisional', basis].",
        "parse": [{"input": s, "expected": parse(s)} for s in PARSE_INPUTS],
        "accession_type": [{"input": s, "expected": accession_type(s)} for s in ACCESSION_TYPE_INPUTS],
        "decide": [], "lookup": [],
    }
    for name, kwargs in DECIDE_CASES:
        d = decide_from(**kwargs)
        vectors["decide"].append({"name": name, "input": kwargs, "expected": {"verdict": d.verdict, "code": d.code}})
    recorded = build_ledger(ROOT)["rows"]
    for include_drafts in (False, True):
        corpora = load_corpora(ROOT, include_drafts=include_drafts)
        for name, kind, catalog_id, identifiers in LOOKUP_CASES:
            ids = [(i[0], i[1]) for i in identifiers]
            prov = tuple((i[0], i[1], i[3]) for i in identifiers if len(i) > 2 and i[2] == "provisional")
            q = Query(name, kind, tuple(ids), tuple(v for _, v in ids), catalog_id, provisional=prov)
            res = search(q, corpora, recorded)
            vectors["lookup"].append({"name": name, "include_drafts": include_drafts, "kind": kind,
                                      "catalog_id": catalog_id, "identifiers": identifiers,
                                      "expected": {"answers": _answers(res), "paper_level_warning": bool(res.warnings)}})
    return vectors


def render() -> str:
    return json.dumps(build(), indent=1, ensure_ascii=False, sort_keys=True) + "\n"


if __name__ == "__main__":
    OUT.write_text(render(), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
