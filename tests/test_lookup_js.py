"""Runs the Node parity tests (tests/js/) against the browser's lookup-core.mjs.

Skipped when `node` isn't on PATH (this is how CI runs it — GitHub's ubuntu runners have
node). No network: both index files are built locally from the committed registry/datasets
before Node ever starts, mirroring how a real `corpus-checker build-site` would produce them.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from corpus_checker.lookup import build_lookup_index

from conftest import REPO


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_node_lookup_core_matches_the_vectors(tmp_path: Path) -> None:
    verified = tmp_path / "lookup-index-verified.json"
    drafts = tmp_path / "lookup-index-drafts.json"
    verified.write_text(json.dumps(build_lookup_index(REPO, include_drafts=False), sort_keys=True, separators=(",", ":")),
                        encoding="utf-8")
    drafts.write_text(json.dumps(build_lookup_index(REPO, include_drafts=True), sort_keys=True, separators=(",", ":")),
                      encoding="utf-8")

    test_files = sorted((REPO / "tests" / "js").glob("*.test.mjs"))
    assert test_files, "no tests/js/*.test.mjs files found"

    result = subprocess.run(
        ["node", "--test", *[str(p) for p in test_files]],
        cwd=REPO,
        env={"LOOKUP_INDEX_VERIFIED": str(verified), "LOOKUP_INDEX_DRAFTS": str(drafts),
             "PATH": __import__("os").environ.get("PATH", "")},
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(result.stdout, file=sys.stderr)
        print(result.stderr, file=sys.stderr)
    assert result.returncode == 0, "node --test tests/js/ failed — see captured output above"
