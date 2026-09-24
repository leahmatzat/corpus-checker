"""Dataset lookup: which models trained on this dataset? Runs offline from the committed cache."""
from __future__ import annotations

import json

import pytest

from corpus_checker.cli import main
from corpus_checker.crosswalk import CrosswalkRecord, _write_cache
from corpus_checker.lookup import answer_text, build_lookup_index, interpret, lookup
from corpus_checker.registry import load_catalog
from corpus_checker.verdict import PAPER_LEVEL_DISCLAIMER

from conftest import REPO, repo_copy

import importlib.util

_spec = importlib.util.spec_from_file_location("make_lookup_vectors", REPO / "scripts" / "make_lookup_vectors.py")
make_lookup_vectors = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(make_lookup_vectors)


def one(results, raw):
    return next(r for r in results if r.query.raw == raw)


def by_model(result):
    return {a.model: a for a in result.answers}


def test_norman_is_in_scfoundation_and_the_answer_is_its_recorded_finding():
    res = one(lookup(["GSE133344"], REPO, network=False), "GSE133344")
    assert res.query.catalog_id == "norman2019"
    a = by_model(res)["scfoundation"]
    assert (a.verdict, a.recorded, a.samples, a.cells) == ("PRESENT", True, 8, 125_081)
    assert a.verified_by == "LM"
    assert res.searched == ("scfoundation",)           # drafts are not searched by default


def test_drafts_only_when_asked_and_marked(tmp_path):
    root = repo_copy(tmp_path)
    assert "toy-draft" not in by_model(one(lookup(["GSE133344"], root, network=False), "GSE133344"))
    res = one(lookup(["GSE133344"], root, network=False, include_drafts=True), "GSE133344")
    stages = {a.stage: a for a in res.answers if a.model == "toy-draft"}
    assert stages["pretraining"].verdict == "NOT CHECKABLE" and stages["pretraining"].draft
    assert stages["fine-tuning"].verdict == "NOT PRESENT"   # the census stage: absence from a superset is provable


def test_a_paper_is_answered_at_paper_level_then_per_linked_dataset():
    results = lookup(["31395745"], REPO, network=False)
    paper, linked = results[0], results[1]
    assert paper.query.kind == "paper"
    a = by_model(paper)["scfoundation"]
    assert (a.verdict, a.code, a.match_level) == ("INCONCLUSIVE", "same_publication", "publication")
    assert "GSE133344" in a.reason
    assert PAPER_LEVEL_DISCLAIMER in paper.warnings
    assert linked.query.raw == "GSE133344" and linked.query.linked_from == "31395745"
    assert by_model(linked)["scfoundation"].verdict == "PRESENT"


def test_zheng_mouse_brain_is_not_in_scfoundation():
    a = by_model(one(lookup(["GSE93421"], REPO, network=False), "GSE93421"))["scfoundation"]
    assert (a.verdict, a.keys_attempted) == ("NOT PRESENT", ("accession", "pmid"))


def test_an_sra_accession_finds_zheng68k_with_its_provisional_identity():
    res = one(lookup(["SRX1723926"], REPO, network=False), "SRX1723926")
    assert res.query.catalog_id == "zheng2017-pbmc68k"
    a = by_model(res)["scfoundation"]
    assert (a.verdict, a.identity, a.recorded) == ("NOT PRESENT", "provisional", True)
    assert "provisional identity" in answer_text(a)


def test_is_it_in_model_x():
    res = one(lookup(["GSE133344"], REPO, network=False, models=["scfoundation"]), "GSE133344")
    assert [a.model for a in res.answers] == ["scfoundation"]


def test_unrecognised_input_gets_no_answers_and_says_why():
    res = one(lookup(["hello"], REPO, network=False), "hello")
    assert res.answers == () and res.query.kind == "unrecognised" and res.query.notes


def test_uuids_are_not_sent_to_ncbi():
    q = interpret("283d65eb-dd53-496d-adb7-7570c7caa443", load_catalog(REPO), network=False)[0]
    assert q.notes == ()


def test_a_sample_query_is_never_widened_to_its_series(tmp_path):
    """GSM3906020 resolves to its series' paper, but must not pick up GSE133344's sibling samples."""
    _write_cache(tmp_path, "geo", "GSM3906020", CrosswalkRecord(
        query="GSM3906020", accessions=("GSE133344", "GSM3906020"), pmids=("31395745",),
        doi="10.1126/science.aax4438", pmcid=None, title=None, organism=None, samples=(), retrieved="2026-09-24", sources=()))
    q = interpret("GSM3906020", load_catalog(REPO), network=False, cache_dir=tmp_path)[0]
    values = {v for _, v in q.identifiers}
    assert "GSE133344" not in values and "GSM3906020" in values and "31395745" in values


def test_answer_text_reads_cleanly():
    res = one(lookup(["283d65eb-dd53-496d-adb7-7570c7caa443"], REPO, network=False, include_drafts=True),
              "283d65eb-dd53-496d-adb7-7570c7caa443")
    for a in res.answers:
        assert ".." not in answer_text(a)
        assert not answer_text(a, lead=False).startswith(("Yes", "No ", "Can't", "Unknown"))


# ------------------------------------------------------------------ CLI

def test_cli_prints_the_coverage_line_and_the_disclaimer_only_when_needed(capsys):
    assert main(["lookup", "GSE93421", "--no-network"]) == 0
    out = capsys.readouterr().out
    assert "Searched 1 entry: scfoundation (verified only)" in out
    assert PAPER_LEVEL_DISCLAIMER not in out
    assert main(["lookup", "31395745", "--no-network"]) == 0
    out = capsys.readouterr().out
    assert PAPER_LEVEL_DISCLAIMER in out and "⚠ paper-level" in out


def test_cli_batch_file_and_usage_errors(tmp_path, capsys):
    ids = tmp_path / "evals.txt"
    ids.write_text("# my benchmark\nGSE133344\n\nGSE93421  # Zheng mouse brain\n")
    assert main(["--format", "json", "lookup", "--file", str(ids), "--no-network"]) == 0
    raws = [r["query"]["raw"] for r in json.loads(capsys.readouterr().out)]
    assert raws == ["GSE133344", "GSE93421"]
    assert main(["lookup"]) == 3
    assert main(["lookup", "GSE1", "--model", "nosuch", "--no-network"]) == 3


# ------------------------------------------------------------------ the web index + parity vectors

def test_web_index_holds_only_verified_entries_and_is_deterministic(tmp_path):
    a, b = build_lookup_index(REPO), build_lookup_index(REPO)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert {c["model"] for c in a["corpora"]} == {"scfoundation"}
    assert a["disclaimer"] == PAPER_LEVEL_DISCLAIMER
    root = repo_copy(tmp_path)
    assert {c["model"] for c in build_lookup_index(root)["corpora"]} == {"scfoundation"}
    assert {c["model"] for c in build_lookup_index(root, include_drafts=True)["corpora"]} == {"scfoundation", "toy-draft"}


def test_parity_vectors_are_current():
    """Other implementations (the site's lookup.js) are tested against this file; it must match the engine."""
    committed = (REPO / "tests" / "fixtures" / "lookup_vectors.json").read_text(encoding="utf-8")
    assert committed == make_lookup_vectors.render(), "run: python scripts/make_lookup_vectors.py"


def test_vectors_cover_every_verdict_code():
    vectors = json.loads((REPO / "tests" / "fixtures" / "lookup_vectors.json").read_text(encoding="utf-8"))
    codes = {c["expected"]["code"] for c in vectors["decide"]}
    assert codes == {"no_manifest", "unconfirmed_source", "present", "superset", "same_publication",
                     "weak_only", "unsearched", "no_exact_key", "missing_required_key", "not_present"}
