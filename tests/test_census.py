"""Type B — a versioned public corpus, via the committed CELLxGENE census 2023-05-15 snapshot.

Uses a synthetic entry that points at the real snapshot, so the resolver and the superset
rule are tested on real data without depending on any draft registry entry.
"""
from __future__ import annotations

import copy

import pytest

from corpus_checker.check import SourceError, check_corpus, locate_committed
from corpus_checker.registry import load_catalog
from corpus_checker.validate import ERROR, Options, validate

from conftest import _ALL_CHECKED, CENSUS_MANIFEST, CENSUS_SNAPSHOT, REPO, TODAY, repo_copy, write_yaml

SNAPSHOT = CENSUS_SNAPSHOT

ENTRY = {
    "schema_version": 2, "id": "census-model", "model": "Census Model", "model_class": "foundation",
    "paper": {"doi": "10.1000/census-model"},
    "discovery": _ALL_CHECKED,
    "evaluated_on": [{"dataset": "norman2019", "task": "perturbation prediction"}],
    "corpora": [{
        "stage": "pretraining",
        "manifest": CENSUS_MANIFEST,
        "result": {"checked": "2026-09-23", "sources_searched": [CENSUS_MANIFEST["sources"][0]["name"]],
                   "findings": [{"dataset": "norman2019", "verdict": "NOT PRESENT", "keys_attempted": ["doi"]}]},
    }],
    "provenance": {"verified_by": "LM", "verified_date": "2026-09-23", "method": "test"},
}
CORPUS = ENTRY["corpora"][0]
CATALOG = load_catalog(REPO)


@pytest.fixture(scope="module")
def run():
    return check_corpus(ENTRY, CORPUS, CATALOG, locate_committed(REPO))


@pytest.fixture(scope="module")
def by_dataset(run):
    return {r.dataset: r for r in run.results}


def test_snapshot_totals(run):
    rows = run.index.records
    assert len(rows) == 562
    assert sum(r.cells for r in rows) == 57_880_760
    assert len({r.project for r in rows}) == 126
    assert sum(1 for r in rows if not r.dois) == 40      # DOI matching cannot see these


@pytest.mark.parametrize("dataset", ["norman2019", "adamson2016", "replogle2022"])
def test_perturbation_benchmarks_absent_from_the_superset(by_dataset, dataset):
    """Absence from the superset is provable."""
    r = by_dataset[dataset]
    assert r.verdict == "NOT PRESENT" and r.keys_attempted == ("doi",)


def test_a_superset_hit_is_never_present(by_dataset):
    r = by_dataset["siletti2022-perirhinal"]
    assert r.matched_on == ("accession", "doi")
    assert r.verdict == "INCONCLUSIVE"
    assert "superset" in r.decision.reason


def test_geo_accessions_are_not_tried_against_cellxgene_uuids(by_dataset):
    """Norman has a confirmed GEO accession, but the census holds only CELLxGENE UUIDs."""
    assert "accession" not in by_dataset["norman2019"].keys_attempted
    assert by_dataset["luecken2021-bmmc"].keys_attempted == ()
    assert by_dataset["luecken2021-bmmc"].verdict == "INCONCLUSIVE"


def test_a_valid_type_b_entry_passes_and_a_geo_attempt_claim_is_rejected(tmp_path):
    root = repo_copy(tmp_path, with_toy_draft=False)
    write_yaml(root / "registry" / "census-model.yaml", ENTRY)

    def errors():
        return {i.code for i in validate(root, opts=Options(today=TODAY))
                if i.severity == ERROR and i.path == "registry/census-model.yaml"}

    assert errors() == set()
    bad = copy.deepcopy(ENTRY)
    bad["corpora"][0]["result"]["findings"][0]["keys_attempted"] = ["accession", "doi"]
    write_yaml(root / "registry" / "census-model.yaml", bad)
    assert "G15" in errors()


def test_an_edited_snapshot_is_caught(tmp_path):
    root = repo_copy(tmp_path, with_toy_draft=False)
    write_yaml(root / "registry" / "census-model.yaml", ENTRY)
    snap = root / SNAPSHOT
    snap.write_text(snap.read_text().replace("10.1101/2022.10.12.511898", "10.1101/0000.00.00.000000"))
    assert "G20" in {i.code for i in validate(root, opts=Options(today=TODAY)) if i.severity == ERROR}
    with pytest.raises(SourceError):
        check_corpus(ENTRY, CORPUS, CATALOG, locate_committed(root))
