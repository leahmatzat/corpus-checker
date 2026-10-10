"""`corpus-checker review`: only a person in a terminal can record a review, and it edits only `review:`."""
from __future__ import annotations

import datetime as dt

from corpus_checker.registry import REVIEW_CHECKS, load_yaml, review_of
from corpus_checker.review import NOT_A_TERMINAL, rewrite_review, run_review

from conftest import repo_copy

DAY = dt.date(2026, 10, 10)


def _answers(*replies):
    it = iter(replies)
    return lambda prompt: next(it)


def _run(root, *replies, terminal=True):
    out: list[str] = []
    code = run_review(root, "scfoundation", ask=_answers(*replies), say=out.append,
                      is_terminal=lambda: terminal, today=DAY)
    return code, "\n".join(out)


def test_it_refuses_without_a_terminal(tmp_path):
    root = repo_copy(tmp_path, with_toy_draft=False)
    before = (root / "registry" / "scfoundation.yaml").read_text()
    code, out = _run(root, terminal=False)
    assert code == 3 and NOT_A_TERMINAL in out
    assert (root / "registry" / "scfoundation.yaml").read_text() == before


def test_it_records_exactly_the_checks_ticked_and_nothing_else(tmp_path):
    root = repo_copy(tmp_path, with_toy_draft=False)
    path = root / "registry" / "scfoundation.yaml"
    before = path.read_text()
    # paper y, manifest y, evaluated_on n, judgment_calls y, plausibility y, initials, confirm
    code, out = _run(root, "y", "y", "n", "y", "y", "LM", "y")
    assert code == 0, out
    by, date, checked = review_of(load_yaml(path))
    assert (by, date, checked) == ("LM", "2026-10-10", ("paper", "manifest", "judgment_calls", "plausibility"))
    changed = [(a, b) for a, b in zip(before.splitlines(), path.read_text().splitlines()) if a != b]
    assert len(before.splitlines()) == len(path.read_text().splitlines())
    assert all(a.startswith("  date:") or a.startswith("  checked:") for a, _ in changed), changed
    assert "Zheng68K: provisional identity" in out and "BMMC multiome (NeurIPS 2021): no PMID exists" in out


def test_unknown_initials_write_nothing(tmp_path):
    root = repo_copy(tmp_path, with_toy_draft=False)
    before = (root / "registry" / "scfoundation.yaml").read_text()
    code, out = _run(root, *("y" * len(REVIEW_CHECKS)), "ZZ")
    assert code == 3 and "isn't in reviewers.yaml" in out
    assert (root / "registry" / "scfoundation.yaml").read_text() == before


def test_declining_at_the_end_writes_nothing(tmp_path):
    root = repo_copy(tmp_path, with_toy_draft=False)
    before = (root / "registry" / "scfoundation.yaml").read_text()
    code, _ = _run(root, *("y" * len(REVIEW_CHECKS)), "LM", "n")
    assert code == 0 and (root / "registry" / "scfoundation.yaml").read_text() == before


def test_rewrite_keeps_a_note_and_everything_around_the_block():
    text = "a: 1\n# comment\nreview:\n  by: LM\n  date: 2026-01-01\n  checked: []\n  note: >\n    kept\n\nprovenance:\n  method: x\n"
    out = rewrite_review(text, "LM", "2026-10-10", ["paper"])
    assert out == ("a: 1\n# comment\nreview:\n  by: LM\n  date: 2026-10-10\n  checked: [paper]\n  note: >\n    kept\n"
                   "\nprovenance:\n  method: x\n")
