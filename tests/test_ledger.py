"""The ledger is a pure inversion of the registry — and carries no model-level score."""
from __future__ import annotations

import json

import pytest

from corpus_checker.ledger import LEDGER_VERSION, build_ledger
from corpus_checker.registry import load_yaml, registry_paths

from conftest import REPO, repo_copy

LEDGER = build_ledger(REPO)


@pytest.fixture(scope="module")
def toy_ledger(tmp_path_factory):
    """The real registry plus a synthetic draft that evaluates on Norman 2019."""
    return build_ledger(repo_copy(tmp_path_factory.mktemp("repo")))


def test_every_finding_becomes_exactly_one_row():
    n = sum(len(c["result"]["findings"]) for p in registry_paths(REPO) for c in load_yaml(p)["corpora"])
    assert len(LEDGER["rows"]) == n


def test_norman_reuse_is_surfaced(toy_ledger):
    reuse = toy_ledger["datasets"]["norman2019"]["reuse"]
    assert {"in_training_of": "scfoundation", "stage": "pretraining", "evaluated_by": "toy-draft"} in reuse


def test_relation_comes_from_evaluated_on(toy_ledger):
    rows = {(r["dataset"], r["model"]): r for r in toy_ledger["rows"]}
    assert rows[("norman2019", "scfoundation")]["relation"] == "reported-eval"
    assert rows[("siletti2022-perirhinal", "toy-draft")]["relation"] == "catalog"


def test_no_model_level_score_anywhere():
    """Invariant 4: verdicts are per (model, stage, dataset). No grade, rank or total per model."""
    forbidden = {"score", "grade", "rank", "overall", "clean", "contaminated"}
    for model in LEDGER["models"].values():
        assert not forbidden & {k.lower() for k in model}


def test_json_round_trip_and_version():
    assert json.loads(json.dumps(LEDGER))["ledger_version"] == LEDGER_VERSION


def test_drafts_are_marked(toy_ledger):
    assert toy_ledger["models"]["scfoundation"]["draft"] is False
    assert toy_ledger["models"]["toy-draft"]["draft"] is True
