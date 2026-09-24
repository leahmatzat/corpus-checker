"""Type B — scGPT against the committed CELLxGENE census 2023-05-15 snapshot."""
from __future__ import annotations

import shutil

import pytest

from corpus_checker.check import SourceError, check_corpus, compare, with_snapshots
from corpus_checker.registry import load_catalog, load_yaml
from corpus_checker.validate import ERROR, Options, validate

from conftest import REPO, TODAY, write_yaml

ENTRY = load_yaml(REPO / "registry" / "scgpt.yaml")
CORPUS = ENTRY["corpora"][0]
CATALOG = load_catalog(REPO)


@pytest.fixture(scope="module")
def run():
    return check_corpus(ENTRY, CORPUS, CATALOG, with_snapshots(REPO))


@pytest.fixture(scope="module")
def by_dataset(run):
    return {r.dataset: r for r in run.results}


def test_registry_reproduces_exactly(run):
    assert compare(CORPUS, run) == []


def test_snapshot_totals(run):
    rows = run.index.records
    assert len(rows) == CORPUS["manifest"]["totals"]["datasets"] == 562
    assert sum(r.cells for r in rows) == CORPUS["manifest"]["totals"]["cells"] == 57_880_760
    assert len({r.project for r in rows}) == CORPUS["manifest"]["totals"]["collections"] == 126
    assert sum(1 for r in rows if not r.dois) == CORPUS["manifest"]["gaps"]["datasets_without_collection_doi"] == 40


@pytest.mark.parametrize("dataset", ["norman2019", "adamson2016", "replogle2022"])
def test_perturbation_evals_absent_from_the_superset(by_dataset, dataset):
    """The original audit's result, reproduced: absence from the superset is provable."""
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


def test_claiming_a_geo_attempt_against_the_census_is_rejected(tmp_path):
    for d in ("schema", "datasets", "snapshots"):
        shutil.copytree(REPO / d, tmp_path / d)
    shutil.copy(REPO / "verifiers.yaml", tmp_path / "verifiers.yaml")
    entry = load_yaml(REPO / "registry" / "scgpt.yaml")
    finding = next(f for f in entry["corpora"][0]["result"]["findings"] if f["dataset"] == "norman2019")
    finding["keys_attempted"] = ["accession", "doi"]
    write_yaml(tmp_path / "registry" / "scgpt.yaml", entry)
    errors = {i.code for i in validate(tmp_path, opts=Options(today=TODAY, allow_draft=True)) if i.severity == ERROR}
    assert "G15" in errors


def test_an_edited_snapshot_is_caught(tmp_path):
    for d in ("schema", "datasets", "snapshots"):
        shutil.copytree(REPO / d, tmp_path / d)
    shutil.copy(REPO / "verifiers.yaml", tmp_path / "verifiers.yaml")
    shutil.copytree(REPO / "registry", tmp_path / "registry")
    snap = tmp_path / CORPUS["manifest"]["sources"][0]["snapshot"]
    snap.write_text(snap.read_text().replace("10.1101/2022.10.12.511898", "10.1101/0000.00.00.000000"))
    errors = {i.code for i in validate(tmp_path, opts=Options(today=TODAY, allow_draft=True)) if i.severity == ERROR}
    assert "G20" in errors
    with pytest.raises(SourceError):
        check_corpus(ENTRY, CORPUS, CATALOG, with_snapshots(tmp_path))
