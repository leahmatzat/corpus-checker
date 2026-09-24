"""G06 — the canary. scFoundation's registry entry must reproduce from its manifest.

Runs the real resolver over the committed derived extracts of Supplementary Data 1 and 2
(extracts/scfoundation/, each tied to its parent file by sha256 — see the registry's `extract:`). If this goes
red, nothing downstream can be trusted.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from corpus_checker.check import SourceError, check_corpus, compare, locate_committed, locate_in_dir
from corpus_checker.registry import load_catalog, load_yaml
from corpus_checker.validate import ERROR, Options, validate

from conftest import REPO, TODAY, write_yaml

EXTRACTS = REPO / "extracts" / "scfoundation"
DOWNLOADS = Path.home() / "Downloads"

ENTRY = load_yaml(REPO / "registry" / "scfoundation.yaml")
CORPUS = ENTRY["corpora"][0]
CATALOG = load_catalog(REPO)


@pytest.fixture(scope="module")
def run():
    return check_corpus(ENTRY, CORPUS, CATALOG, locate_committed(REPO))


@pytest.fixture(scope="module")
def by_dataset(run):
    return {r.dataset: r for r in run.results}


def test_norman_is_present_at_8_samples_125081_cells(by_dataset):
    r = by_dataset["norman2019"]
    assert r.verdict == "PRESENT"
    assert r.relation == "self-eval"
    assert r.matched_on == ("accession", "pmid")          # both keys hit, independently
    assert r.overlap_level == "sample"
    assert r.sample_ids == tuple(f"GSM39060{n}" for n in range(20, 28))
    assert r.cells == 125_081


@pytest.mark.parametrize("dataset", ["adamson2016", "dixit2016", "baron2016", "segerstolpe2016"])
def test_absent_on_both_keys(by_dataset, dataset):
    r = by_dataset[dataset]
    assert r.verdict == "NOT PRESENT"
    assert r.keys_attempted == ("accession", "pmid")


def test_zheng68k_is_absent_with_a_provisional_identity(by_dataset):
    """Searched on its SRA experiment and on its PMID; the SRA record is identified by title and submitter only."""
    r = by_dataset["zheng2017-pbmc68k"]
    assert r.verdict == "NOT PRESENT"
    assert r.keys_attempted == ("accession", "pmid")
    assert r.identity == "provisional" and "Fresh 68k PBMCs" in r.identity_basis


def test_registry_reproduces_exactly(run):
    assert compare(CORPUS, run) == []


def test_manifest_totals_match_the_registry(run):
    sample_rows = [r for r in run.index.records if r.source == "Supplementary Data 1"]
    totals = CORPUS["manifest"]["totals"]
    assert len(sample_rows) == totals["samples"] == 10_747
    assert sum(r.cells for r in sample_rows) == totals["cells"] == 55_040_785
    assert len({r.project for r in sample_rows}) == totals["projects"] == 656


def test_manifest_gaps_match_the_registry(run):
    """The reasons single-key matching is unsafe here, recomputed rather than trusted."""
    study_rows = [r for r in run.index.records if r.source == "Supplementary Data 2"]
    sample_projects = {r.project for r in run.index.records if r.source == "Supplementary Data 1"}
    gaps = CORPUS["manifest"]["gaps"]
    assert sum(1 for r in study_rows if not r.pmids and not r.dois) == gaps["blank_pmids_in_study_table"] == 161
    assert len(sample_projects - {r.project for r in study_rows}) == gaps["projects_absent_from_study_table"] == 134
    raw = [line.rsplit(",", 1)[-1] for line in (EXTRACTS / "data2.csv").read_text(encoding="utf-8").splitlines()
           if not line.startswith("#")][1:]
    assert sum(1 for cell in raw if cell and not cell.strip().isdigit()) == gaps["multi_id_pmid_cells"] == 21


def test_engine_output_always_passes_the_validator(run, tmp_path):
    """The engine must never produce a finding the validator rejects."""
    shutil.copytree(REPO / "schema", tmp_path / "schema")
    shutil.copytree(REPO / "datasets", tmp_path / "datasets")
    shutil.copytree(REPO / "extracts", tmp_path / "extracts")
    shutil.copy(REPO / "verifiers.yaml", tmp_path / "verifiers.yaml")
    entry = load_yaml(REPO / "registry" / "scfoundation.yaml")
    entry["corpora"][0]["result"]["findings"] = [r.as_finding() for r in run.results]
    write_yaml(tmp_path / "registry" / "scfoundation.yaml", entry)
    errors = [i for i in validate(tmp_path, opts=Options(today=TODAY)) if i.severity == ERROR]
    assert errors == []


def _repo_copy(tmp_path):
    for d in ("schema", "datasets", "registry", "extracts"):
        shutil.copytree(REPO / d, tmp_path / d)
    shutil.copy(REPO / "verifiers.yaml", tmp_path / "verifiers.yaml")
    return tmp_path


def test_an_extract_for_a_different_file_is_refused(tmp_path):
    root = _repo_copy(tmp_path)
    ext = root / "extracts" / "scfoundation" / "data1.csv"
    ext.write_text(ext.read_text().replace(CORPUS["manifest"]["sources"][0]["sha256"], "0" * 64))
    with pytest.raises(SourceError):
        check_corpus(ENTRY, CORPUS, CATALOG, locate_committed(root))
    assert "G22" in {i.code for i in validate(root, opts=Options(today=TODAY)) if i.severity == ERROR}


def test_a_missing_source_is_an_error_not_a_partial_search(tmp_path):
    root = _repo_copy(tmp_path)
    (root / "extracts" / "scfoundation" / "data2.csv").unlink()
    with pytest.raises(SourceError):
        check_corpus(ENTRY, CORPUS, CATALOG, locate_committed(root))


_ORIGINALS = [DOWNLOADS / "41592_2024_2305_MOESM4_ESM.xlsx", DOWNLOADS / "41592_2024_2305_MOESM5_ESM.xlsx"]


@pytest.mark.skipif(not all(p.is_file() for p in _ORIGINALS), reason="publisher XLSX files not present locally")
def test_original_xlsx_gives_the_same_answer(by_dataset):
    """Reading the publisher files directly must agree with the committed extracts."""
    direct = check_corpus(ENTRY, CORPUS, CATALOG, locate_committed(REPO, locate_in_dir(DOWNLOADS)))
    for r in direct.results:
        e = by_dataset[r.dataset]
        assert (r.verdict, r.keys_attempted, r.matched_on, r.sample_ids, r.cells) == \
               (e.verdict, e.keys_attempted, e.matched_on, e.sample_ids, e.cells)
