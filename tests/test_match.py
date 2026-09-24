"""Normalisation and matching on the cell shapes real manifests contain."""
from __future__ import annotations

import pytest

from corpus_checker import normalize
from corpus_checker.match import RecordIndex, match
from corpus_checker.resolvers import CanonicalRecord
from corpus_checker.verdict import decide


@pytest.mark.parametrize("cell,expected", [
    ("GEO-GSE133344", ("GSE133344",)),
    ("GEO-GSM3906020", ("GSM3906020",)),
    ("DISCO-GSE180595", ("GSE180595",)),
    ("AE-E-MTAB-11536", ("E-MTAB-11536",)),
    ("DISCO-E-MTAB-9492", ("E-MTAB-9492",)),
    ("EGAS00001004107", ("EGAS00001004107",)),
    ("DISCO-HRA000150", ("HRA000150",)),
    ("HCA-HeartSingleCellsAndNucleiSeq", ()),
    ("TSP1_BLOOD_1", ()),
    ("283D65EB-DD53-496D-ADB7-7570C7CAA443", ("283d65eb-dd53-496d-adb7-7570c7caa443",)),
    (None, ()),
])
def test_accession_tokens(cell, expected):
    assert normalize.accessions(cell) == expected


@pytest.mark.parametrize("cell,pmids,dois", [
    (31395745, ("31395745",), ()),
    (31395745.0, ("31395745",), ()),
    ("31395745.0", ("31395745",), ()),
    ("31067475 || 33521016", ("31067475", "33521016"), ()),
    ("33294861 || 33705361 || 34349032", ("33294861", "33705361", "34349032"), ()),
    ("31924475;34099645", ("31924475", "34099645"), ()),
    ("doi:10.1016/j.jhep.2020.05.039", (), ("10.1016/j.jhep.2020.05.039",)),
    ("", (), ()),
])
def test_pmid_cells(cell, pmids, dois):
    assert normalize.pmids(cell) == pmids
    assert normalize.dois(cell) == dois


def _dataset(*idents):
    return {"id": "x", "identifiers": [{"type": t, "value": v, "confirmed": c} for t, v, c in idents]}


def test_no_substring_matches():
    """`"GSE9054" in "GSE90546"` is True. The matcher must say no."""
    index = RecordIndex([CanonicalRecord(accessions=normalize.accessions("GEO-GSE90546"), source="s", row=1)])
    m = match(index, _dataset(("geo", "GSE9054", True)), ["accession"])
    assert not m.any_hit


def test_every_key_is_attempted_and_reported():
    index = RecordIndex([
        CanonicalRecord(accessions=("GSE1",), project="P1", source="study", row=1),
        CanonicalRecord(pmids=("111",), project="P1", source="study", row=2),
    ])
    m = match(index, _dataset(("geo", "GSE1", True), ("pmid", "111", True)), ["accession", "pmid"])
    assert m.keys_hit == ("accession", "pmid")


def test_unconfirmed_identifiers_are_never_used():
    index = RecordIndex([CanonicalRecord(accessions=("SRP1",), source="s", row=1)])
    m = match(index, _dataset(("sra", "SRP1", False), ("pmid", "9", True)), ["accession", "pmid"])
    assert not m.any_hit


def test_study_hit_expands_to_its_samples_but_sample_hit_does_not():
    rows = [
        CanonicalRecord(accessions=("GSE1",), project="GEO-GSE1", source="study", row=1),
        CanonicalRecord(accessions=("GSE1", "GSM1"), project="GEO-GSE1", sample="GEO-GSM1", cells=10, source="samples", row=1),
        CanonicalRecord(accessions=("GSE1", "GSM2"), project="GEO-GSE1", sample="GEO-GSM2", cells=5, source="samples", row=2),
    ]
    index = RecordIndex(rows)
    whole = match(index, _dataset(("geo", "GSE1", True)), ["accession"])
    assert whole.sample_ids == ("GSM1", "GSM2") and whole.cells == 15
    one = match(index, _dataset(("geo", "GSM2", True)), ["accession"])
    assert one.sample_ids == ("GSM2",) and one.cells == 5


def test_superset_hit_is_never_present():
    index = RecordIndex([CanonicalRecord(dois=("10.1/x",), source="census", row=1)])
    m = match(index, _dataset(("doi", "10.1/X", True)), ["doi"])
    assert m.any_hit
    assert decide(manifest_type="B", can_prove_presence=False, match=m).verdict == "INCONCLUSIVE"


def test_unconfirmed_source_caps_both_directions():
    empty = match(RecordIndex([]), _dataset(("geo", "GSE1", True)), ["accession"])
    assert decide(manifest_type="A", can_prove_presence=True, match=empty,
                  unconfirmed_sources=("S1",)).verdict == "INCONCLUSIVE"


def test_a_paper_level_match_is_not_presence():
    """Same paper, different dataset: the manifest lists GSE2, the query is GSE1, both under PMID 7."""
    index = RecordIndex([CanonicalRecord(accessions=("GSE2",), pmids=("7",), project="GEO-GSE2", source="study", row=1)])
    m = match(index, _dataset(("geo", "GSE1", True), ("pmid", "7", True)), ["accession", "pmid"])
    assert m.match_level == "publication" and m.listed_accessions == ("GSE2",)
    d = decide(manifest_type="A", can_prove_presence=True, match=m)
    assert (d.verdict, d.code) == ("INCONCLUSIVE", "same_publication")
    assert "GSE2" in d.reason


def test_an_accession_match_is_dataset_level():
    index = RecordIndex([CanonicalRecord(accessions=("GSE1",), pmids=("7",), project="GEO-GSE1", source="study", row=1)])
    m = match(index, _dataset(("geo", "GSE1", True), ("pmid", "7", True)), ["accession", "pmid"])
    assert m.match_level == "dataset"
    assert decide(manifest_type="A", can_prove_presence=True, match=m).verdict == "PRESENT"
