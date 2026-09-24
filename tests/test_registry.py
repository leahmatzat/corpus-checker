"""The real registry/ and datasets/, validated as committed.

The expected error sets are the OPEN ISSUES in the data. When a verifier resolves one,
this test is updated in the same PR — so an issue can't disappear silently.
"""
from __future__ import annotations

import pytest

from corpus_checker.registry import load_yaml, registry_paths, relation
from corpus_checker.validate import ERROR, Options, validate

from conftest import REPO, TODAY

EXPECTED_OPEN_ERRORS = {
    "registry/scfoundation.yaml": set(),
    # Drafts: nobody has verified them.
    "registry/geneformer-30m.yaml": {"G10"},
    "registry/scgpt.yaml": {"G10"},
}


@pytest.fixture(scope="module")
def issues():
    return validate(REPO, opts=Options(today=TODAY))


def test_datasets_are_valid(issues):
    assert [i for i in issues if i.path.startswith("datasets/")] == []


def test_every_entry_is_schema_valid(issues):
    assert [i for i in issues if i.code == "G00"] == []


@pytest.mark.parametrize("path", sorted(EXPECTED_OPEN_ERRORS))
def test_open_errors_are_exactly_the_known_ones(issues, path):
    got = {i.code for i in issues if i.path == path and i.severity == ERROR}
    assert got == EXPECTED_OPEN_ERRORS[path]


def test_no_unexpected_entries_with_errors(issues):
    with_errors = {i.path for i in issues if i.severity == ERROR}
    assert with_errors <= set(EXPECTED_OPEN_ERRORS)


def test_relation_is_derived_from_evaluated_on():
    entries = {p.stem: load_yaml(p) for p in registry_paths(REPO)}
    assert relation(entries["scfoundation"], "norman2019") == "self-eval"
    assert relation(entries["geneformer-30m"], "baron2016") == "catalog"
