"""Every guard, fired against a registry that is valid except for one mutation.

Each case names the guard it expects. If a guard stops firing, its case fails — so
anyone can re-run `pytest` to confirm every guard in docs/RULES.md still works.
"""
from __future__ import annotations

import hashlib
import subprocess
import urllib.error

import pytest

from corpus_checker.validate import ERROR, WARNING, Options, validate

from conftest import S1_BYTES, S2_BYTES, TODAY, write_yaml


def run(repo, entry=None, **opts):
    if entry is not None:
        write_yaml(repo / "registry" / "toy.yaml", entry)
    return validate(repo, opts=Options(today=TODAY, **opts))


def codes(issues, severity=ERROR):
    return {i.code for i in issues if i.severity == severity}


def corpus(e):
    return e["corpora"][0]


def finding(e, i):
    return corpus(e)["result"]["findings"][i]


def test_base_registry_is_clean(toy_repo):
    assert run(toy_repo) == []


# ------------------------------------------------------------------ mutations

def _type_b_present(e):
    m = corpus(e)["manifest"]
    for k in ("sources", "requires_keys", "identifier_type"):
        m.pop(k)
    m.update(type="B", pointer={"corpus": "census", "release": "2023-05-15"}, keys_available=["accession", "pmid", "doi"])


def _c_vague(e, verdict="NOT CHECKABLE"):
    m = corpus(e)["manifest"]
    for k in ("sources", "requires_keys", "keys_available", "resolver", "identifier_type"):
        m.pop(k)
    m["type"] = "C-vague"
    corpus(e)["result"]["sources_searched"] = []
    corpus(e)["result"]["findings"] = [{"dataset": "alpha2020", "verdict": verdict}]


def _supplied(e, method="none"):
    src = corpus(e)["manifest"]["sources"][1]
    src.update(acquired="supplied", supplied_by="LM", confirmation={"method": method})


CASES = [
    # --- G00: rules enforced by the JSON Schema itself
    ("G00", "type-B manifest returns PRESENT (superset rule)", _type_b_present),
    ("G00", "C-vague manifest returns NOT PRESENT", lambda e: _c_vague(e, "NOT PRESENT")),
    ("G00", "type-A manifest with empty sources_searched", lambda e: corpus(e)["result"].update(sources_searched=[])),
    ("G00", "PRESENT without samples/sample_ids/cells", lambda e: finding(e, 0).pop("samples")),
    ("G00", "PRESENT without overlap_level", lambda e: finding(e, 0).pop("overlap_level")),
    ("G00", "INCONCLUSIVE without a reason", lambda e: finding(e, 1).update(verdict="INCONCLUSIVE")),
    ("G00", "NOT PRESENT without keys_attempted", lambda e: finding(e, 1).pop("keys_attempted")),
    ("G00", "manifest source without sha256", lambda e: corpus(e)["manifest"]["sources"][0].pop("sha256")),
    ("G00", "supplied source without supplied_by", lambda e: (_supplied(e), corpus(e)["manifest"]["sources"][1].pop("supplied_by"))),
    ("G00", "fetched source claiming totals confirmation",
     lambda e: corpus(e)["manifest"]["sources"][0].update(confirmation={"method": "totals", "detail": "x", "date": "2026-09-01"})),
    ("G00", "totals confirmation without detail", lambda e: _supplied(e, "totals")),
    ("G00", "discovery location omitted", lambda e: e["discovery"].pop("supplementary_files")),
    ("G00", "checked discovery location without 'found'", lambda e: e["discovery"]["methods"].pop("found")),
    ("G00", "foundation model with no corpora", lambda e: e.update(corpora=[])),
    ("G00", "manifest.quote missing", lambda e: corpus(e)["manifest"].pop("quote")),
    ("G00", "verdict outside the four", lambda e: finding(e, 1).update(verdict="CLEAN")),
    # --- G01: exact-key rule
    ("G01", "PRESENT matched on title only",
     lambda e: finding(e, 0).update(keys_attempted=["title"], matched_on=["title"])),
    ("G01", "NOT PRESENT from a keyword-only attempt",
     lambda e: (corpus(e)["manifest"]["keys_available"].append("keyword"), finding(e, 1).update(keys_attempted=["keyword"]))),
    # --- G02: dangling references
    ("G02", "finding for a dataset not in datasets/", lambda e: finding(e, 1).update(dataset="nowhere2000")),
    ("G02", "evaluated_on a dataset not in datasets/", lambda e: e["evaluated_on"].append({"dataset": "nowhere2000", "task": "x"})),
    # --- G07 / G08: sources_searched
    ("G07", "sources_searched names an unknown file", lambda e: corpus(e)["result"]["sources_searched"].append("S3")),
    ("G08", "NOT PRESENT from one of two manifest files", lambda e: corpus(e)["result"].update(sources_searched=["S1"])),
    # --- G09: unconfirmed manifest source
    ("G09", "definite verdicts from an unconfirmed supplied file", _supplied),
    # --- G10: provenance
    ("G10", "verified_by empty", lambda e: e["provenance"].update(verified_by="", verified_date=None)),
    ("G10", "verified_by not a listed verifier", lambda e: e["provenance"].update(verified_by="ZZ")),
    # --- G11: NOT CHECKABLE needs full discovery
    ("G11", "NOT CHECKABLE with an unchecked discovery location",
     lambda e: (_c_vague(e), e["discovery"].update(github_repo={"checked": False}))),
    # --- G12: id
    ("G12", "id does not match file name", lambda e: e.update(id="other")),
    # --- G13: keys outside what the manifest offers
    ("G13", "attempted a key the manifest does not carry",
     lambda e: finding(e, 1).update(keys_attempted=["accession", "pmid", "doi"])),
    ("G13", "matched_on not a subset of keys_attempted",
     lambda e: finding(e, 0).update(matched_on=["accession", "title"])),
    # --- G14: dual-key rule
    ("G14", "NOT PRESENT on accession alone when pmid is required",
     lambda e: finding(e, 1).update(keys_attempted=["accession"])),
    ("G14", "NOT PRESENT for a PMID-only dataset (the Zheng68K case)",
     lambda e: corpus(e)["result"]["findings"].append({"dataset": "beta2021", "verdict": "NOT PRESENT", "keys_attempted": ["pmid"]})),
    # --- G15: claimed attempt with no confirmed identifier behind it
    ("G15", "claims an accession attempt using an unconfirmed accession",
     lambda e: corpus(e)["result"]["findings"].append(
         {"dataset": "beta2021", "verdict": "NOT PRESENT", "keys_attempted": ["accession", "pmid"]})),
    # --- G16: duplicate finding
    ("G16", "same dataset twice in one corpus",
     lambda e: corpus(e)["result"]["findings"].append(
         {"dataset": "gamma2022", "verdict": "NOT PRESENT", "keys_attempted": ["accession", "pmid"]})),
    # --- G19: roles must name real columns
    ("G19", "a role names a column that is not in columns",
     lambda e: corpus(e)["manifest"]["sources"][0].update(columns=["project_ID"], roles={"accession": ["projectID"]})),
    # --- G20: committed snapshot must exist and match
    ("G20", "committed snapshot missing",
     lambda e: corpus(e)["manifest"]["sources"][0].update(snapshot="snapshots/missing.csv")),
    # --- G22: committed extract must exist and name its parent file
    ("G22", "committed extract missing",
     lambda e: corpus(e)["manifest"]["sources"][0].update(extract="extracts/missing.csv")),
    # --- G21: a PMID or DOI names a paper, not a dataset
    ("G21", "PRESENT on a PMID-only match",
     lambda e: finding(e, 0).update(matched_on=["pmid"], match_level="publication")),
    ("G21", "match_level dataset claimed without an accession hit",
     lambda e: finding(e, 0).update(matched_on=["pmid"])),
    ("G00", "PRESENT without match_level", lambda e: finding(e, 0).pop("match_level")),
    # --- G23: identity is derived from the catalog
    ("G23", "attempted a provisional accession but recorded no provisional identity",
     lambda e: corpus(e)["result"]["findings"].append(
         {"dataset": "epsilon2024", "verdict": "NOT PRESENT", "keys_attempted": ["accession", "pmid"]})),
    ("G23", "claims provisional identity where every identifier is confirmed",
     lambda e: finding(e, 1).update(identity="provisional", identity_basis="none really")),
    ("G00", "provisional identity without its basis",
     lambda e: finding(e, 1).update(identity="provisional")),
    # --- G15 via accession_types: a GEO accession is not an attempt against a UUID-keyed manifest
    ("G15", "accession attempt with the wrong kind of accession",
     lambda e: corpus(e)["manifest"].update(accession_types=["cellxgene_collection"], requires_keys=["pmid"])),
]

WARNING_CASES = [
    ("G05", "manifest as_of older than the threshold", lambda e: corpus(e)["manifest"].update(as_of="2024-01-01")),
    ("G17", "reported-eval dataset never checked", lambda e: e["evaluated_on"].append({"dataset": "beta2021", "task": "x"})),
    ("G18", "NOT PRESENT while discovery is incomplete", lambda e: e["discovery"].update(hf_model_card={"checked": False})),
]


@pytest.mark.parametrize("code,why,mutate", CASES, ids=[f"{c}-{w}" for c, w, _ in CASES])
def test_guard_fires(toy_repo, entry, code, why, mutate):
    mutate(entry)
    issues = run(toy_repo, entry)
    assert code in codes(issues), f"{code} did not fire for: {why}\n{issues}"


@pytest.mark.parametrize("code,why,mutate", WARNING_CASES, ids=[f"{c}-{w}" for c, w, _ in WARNING_CASES])
def test_warning_fires_without_blocking(toy_repo, entry, code, why, mutate):
    mutate(entry)
    issues = run(toy_repo, entry)
    assert code in codes(issues, WARNING), f"{code} did not warn for: {why}\n{issues}"
    assert not codes(issues), f"a warning case must not produce errors: {issues}"


def test_every_documented_guard_has_a_case():
    from corpus_checker import validate as v
    documented = {line.split()[0] for line in v.__doc__.splitlines() if line.strip().startswith("G")}
    covered = {c for c, _, _ in CASES + WARNING_CASES} | {"G03", "G04", "G06"}   # G03/G04 below; G06 is the canary
    assert documented <= covered, f"guards with no test: {sorted(documented - covered)}"


def test_allow_draft_downgrades_empty_signer(toy_repo, entry):
    entry["provenance"].update(verified_by="", verified_date=None)
    issues = run(toy_repo, entry, allow_draft=True)
    assert "G10" in codes(issues, WARNING) and "G10" not in codes(issues)


def test_type_b_can_still_prove_absence(toy_repo, entry):
    """The superset rule forbids PRESENT, not NOT PRESENT."""
    _type_b_present(entry)
    corpus(entry)["result"]["findings"] = [
        {"dataset": "gamma2022", "verdict": "NOT PRESENT", "keys_attempted": ["accession", "pmid", "doi"]}]
    corpus(entry)["result"]["sources_searched"] = ["census 2023-05-15"]
    entry["evaluated_on"] = [{"dataset": "gamma2022", "task": "x"}]
    assert codes(run(toy_repo, entry)) == set()


def test_identity_note_lets_a_verifier_accept_a_paper_level_match(toy_repo, entry):
    finding(entry, 0).update(matched_on=["pmid"], match_level="publication",
                             identity_note="The paper published a single dataset, GSE1 (checked on GEO).")
    assert "G21" not in codes(run(toy_repo, entry))


def test_provisional_identity_recorded_with_its_basis_passes(toy_repo, entry):
    corpus(entry)["result"]["findings"].append(
        {"dataset": "epsilon2024", "verdict": "NOT PRESENT", "keys_attempted": ["accession", "pmid"],
         "identity": "provisional", "identity_basis": "matched on SRA title and submitter"})
    assert codes(run(toy_repo, entry)) == set()


def test_totals_confirmation_allows_definite_verdicts(toy_repo, entry):
    src = corpus(entry)["manifest"]["sources"][1]
    src.update(acquired="supplied", supplied_by="LM",
               confirmation={"method": "totals", "detail": "sums to the published count", "date": "2026-09-23"})
    assert codes(run(toy_repo, entry)) == set()


# ------------------------------------------------------------------ datasets/

def test_dataset_schema_and_id(toy_repo):
    bad = {"schema_version": 1, "id": "wrong", "name": "X", "identifiers": [{"type": "geo", "value": "GSE1", "confirmed": True}]}
    write_yaml(toy_repo / "datasets" / "delta2023.yaml", bad)
    assert "G12" in codes(run(toy_repo))
    bad = {"schema_version": 1, "id": "delta2023", "name": "X", "identifiers": [{"type": "geo", "value": "not-a-gse", "confirmed": True}]}
    write_yaml(toy_repo / "datasets" / "delta2023.yaml", bad)
    assert "G00" in codes(run(toy_repo))


# ------------------------------------------------------------------ G04 source drift

def _fetcher(payloads):
    def fetch(url):
        name = url.rsplit("/", 1)[1].split(".")[0]
        result = payloads[name]
        if isinstance(result, Exception):
            raise result
        return result
    return fetch


def test_g04_hash_mismatch_is_an_error(toy_repo):
    issues = run(toy_repo, check_sources=True, fetch=_fetcher({"S1": S1_BYTES, "S2": b"moved"}))
    assert "G04" in codes(issues)


def test_g04_unreachable_is_only_a_warning(toy_repo):
    issues = run(toy_repo, check_sources=True, fetch=_fetcher({"S1": S1_BYTES, "S2": urllib.error.URLError("403")}))
    assert "G04" in codes(issues, WARNING) and "G04" not in codes(issues)


def test_g04_matching_supplied_file_suggests_upgrade(toy_repo, entry):
    src = corpus(entry)["manifest"]["sources"][1]
    src.update(acquired="supplied", supplied_by="LM",
               confirmation={"method": "totals", "detail": "d", "date": "2026-09-23"})
    issues = run(toy_repo, entry, check_sources=True, fetch=_fetcher({"S1": S1_BYTES, "S2": S2_BYTES}))
    assert "G04" in codes(issues, WARNING) and not codes(issues)


# ------------------------------------------------------------------ G03 stale verification

def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"})


@pytest.fixture
def git_repo(toy_repo):
    _git(toy_repo, "init", "-q", "-b", "main")
    _git(toy_repo, "add", "-A")
    _git(toy_repo, "commit", "-q", "-m", "base")
    return toy_repo


def test_g03_verdict_edit_without_resign_is_blocked(git_repo, entry):
    finding(entry, 1)["verdict"] = "INCONCLUSIVE"
    finding(entry, 1)["reason"] = "third party thinks so"
    assert "G03" in codes(run(git_repo, entry, base_ref="main"))


def test_g03_resign_clears_it(git_repo, entry):
    finding(entry, 1)["verdict"] = "INCONCLUSIVE"
    finding(entry, 1)["reason"] = "re-checked"
    entry["provenance"]["verified_date"] = "2026-09-23"
    assert "G03" not in codes(run(git_repo, entry, base_ref="main"))


def test_g03_manifest_edit_counts_too(git_repo, entry):
    corpus(entry)["manifest"]["sources"][0]["url"] = "https://example.org/elsewhere.xlsx"
    assert "G03" in codes(run(git_repo, entry, base_ref="main"))


def test_g03_non_verdict_edit_needs_no_resign(git_repo, entry):
    entry["paper"]["note"] = "typo fix"
    entry["discovery"]["methods"]["found"] = "clarified"
    assert "G03" not in codes(run(git_repo, entry, base_ref="main"))


def test_g03_follows_renames(git_repo, entry):
    (git_repo / "registry" / "toy.yaml").unlink()
    entry["id"] = "toy-v1"
    finding(entry, 1)["verdict"] = "INCONCLUSIVE"
    finding(entry, 1)["reason"] = "edited during rename"
    write_yaml(git_repo / "registry" / "toy-v1.yaml", entry)
    _git(git_repo, "add", "-A")
    issues = validate(git_repo, opts=Options(today=TODAY, base_ref="main"))
    assert "G03" in codes(issues)
