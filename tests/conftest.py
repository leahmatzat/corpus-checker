"""A throwaway repo with a small, fully valid registry that each guard test breaks one way."""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import shutil
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
TODAY = dt.date(2026, 9, 23)

S1_BYTES, S2_BYTES = b"source one", b"source two"

DATASETS = {
    "alpha2020": {
        "schema_version": 1, "id": "alpha2020", "name": "Alpha 2020",
        "identifiers": [
            {"type": "geo", "value": "GSE1", "confirmed": True},
            {"type": "pmid", "value": "1", "confirmed": True},
        ],
    },
    "beta2021": {   # PMID only — like Zheng68K
        "schema_version": 1, "id": "beta2021", "name": "Beta 2021",
        "identifiers": [{"type": "pmid", "value": "2", "confirmed": True},
                        {"type": "sra", "value": "SRP2", "confirmed": False}],
    },
    "epsilon2024": {   # accession known only provisionally — like Zheng68K's SRA experiment
        "schema_version": 1, "id": "epsilon2024", "name": "Epsilon 2024",
        "identifiers": [
            {"type": "sra", "value": "SRX5", "confirmed": "provisional", "basis": "matched on SRA title and submitter"},
            {"type": "pmid", "value": "5", "confirmed": True},
        ],
    },
    "gamma2022": {
        "schema_version": 1, "id": "gamma2022", "name": "Gamma 2022",
        "identifiers": [
            {"type": "geo", "value": "GSE3", "confirmed": True},
            {"type": "pmid", "value": "3", "confirmed": True},
            {"type": "doi", "value": "10.1000/gamma", "confirmed": True},
        ],
    },
}


def _checked(found: str = "none") -> dict:
    return {"checked": True, "found": found}


def _source(name: str, payload: bytes) -> dict:
    return {
        "name": name, "url": f"https://example.org/{name}.xlsx", "format": "xlsx",
        "sha256": hashlib.sha256(payload).hexdigest(),
        "acquired": "fetched", "confirmation": {"method": "url_hash", "date": "2026-09-01"},
    }


ENTRY = {
    "schema_version": 2,
    "id": "toy",
    "model": "Toy",
    "model_class": "foundation",
    "paper": {"doi": "10.1000/toy"},
    "discovery": {k: _checked() for k in (
        "data_availability", "code_availability", "supplementary_files", "methods", "github_repo",
        "hf_model_card", "hf_dataset_card", "archive_deposits", "preprint_vs_published")},
    "evaluated_on": [{"dataset": "alpha2020", "task": "clustering"}],
    "corpora": [{
        "stage": "pretraining",
        "manifest": {
            "type": "A", "identifier_type": "accession", "quote": "the list is in Supplementary Data 1 and 2",
            "as_of": "2026-09-01", "resolver": "xlsx_two_table",
            "keys_available": ["accession", "pmid", "title"],
            "requires_keys": ["accession", "pmid"],
            "sources": [_source("S1", S1_BYTES), _source("S2", S2_BYTES)],
        },
        "result": {
            "checked": "2026-09-01",
            "sources_searched": ["S1", "S2"],
            "findings": [
                {"dataset": "alpha2020", "verdict": "PRESENT", "keys_attempted": ["accession", "pmid"],
                 "matched_on": ["accession"], "match_level": "dataset", "overlap_level": "sample", "samples": 1},
                {"dataset": "gamma2022", "verdict": "NOT PRESENT", "keys_attempted": ["accession", "pmid"]},
            ],
        },
    }],
    "provenance": {"verified_by": "LM", "verified_date": "2026-09-01", "method": "test"},
}


def write_yaml(path: Path, doc: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


@pytest.fixture
def toy_repo(tmp_path: Path) -> Path:
    shutil.copytree(REPO / "schema", tmp_path / "schema")
    shutil.copy(REPO / "verifiers.yaml", tmp_path / "verifiers.yaml")
    for ds in DATASETS.values():
        write_yaml(tmp_path / "datasets" / f"{ds['id']}.yaml", ds)
    write_yaml(tmp_path / "registry" / "toy.yaml", ENTRY)
    return tmp_path


@pytest.fixture
def entry() -> dict:
    return copy.deepcopy(ENTRY)
