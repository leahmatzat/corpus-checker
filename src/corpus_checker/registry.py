"""Loading registry/*.yaml, datasets/*.yaml and the schemas that govern them.

registry/ and datasets/ are the only hand-authored data. Everything else — the
ledger, the site — is derived from what this module loads.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

EXACT_KEYS = frozenset({"accession", "pmid", "doi"})
WEAK_KEYS = frozenset({"title", "keyword"})

# datasets/ identifier type -> the match key it can satisfy. None = not matchable.
_IDENTIFIER_KEY = {"pmid": "pmid", "doi": "doi", "url": None}


class _NoTimestampLoader(yaml.SafeLoader):
    """SafeLoader that leaves dates as strings.

    YAML would otherwise turn `as_of: 2026-09-08` into a datetime.date, which the
    JSON Schema (type: string, format: date) rejects and json.dumps cannot compare.
    """


_NoTimestampLoader.yaml_implicit_resolvers = {
    first: [r for r in resolvers if r[0] != "tag:yaml.org,2002:timestamp"]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def load_yaml_text(text: str) -> Any:
    return yaml.load(text, Loader=_NoTimestampLoader)


def load_yaml(path: Path) -> Any:
    return load_yaml_text(Path(path).read_text(encoding="utf-8"))


def repo_root(start: Path | None = None) -> Path:
    """The directory holding schema/registry.schema.json.

    Honours $CORPUS_CHECKER_ROOT, then walks up from `start` (default: cwd), then
    falls back to the source checkout this package was installed from.
    """
    if env := os.environ.get("CORPUS_CHECKER_ROOT"):
        return Path(env).resolve()
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / "schema" / "registry.schema.json").is_file():
            return candidate
    return Path(__file__).resolve().parents[2]


@lru_cache(maxsize=None)
def load_schema(root: Path, name: str) -> dict:
    return json.loads((root / "schema" / name).read_text(encoding="utf-8"))


def registry_paths(root: Path) -> list[Path]:
    return sorted((root / "registry").glob("*.yaml"))


def dataset_paths(root: Path) -> list[Path]:
    return sorted((root / "datasets").glob("*.yaml"))


def load_catalog(root: Path) -> dict[str, dict]:
    """datasets/ keyed by id. Malformed files are skipped here; validate reports them."""
    catalog: dict[str, dict] = {}
    for path in dataset_paths(root):
        doc = load_yaml(path)
        if isinstance(doc, dict) and isinstance(doc.get("id"), str):
            catalog[doc["id"]] = doc
    return catalog


def load_verifiers(root: Path) -> set[str]:
    path = root / "verifiers.yaml"
    if not path.is_file():
        return set()
    doc = load_yaml(path) or {}
    return {v["initials"] for v in doc.get("verifiers", []) if v.get("initials")}


def identifier_key(identifier_type: str) -> str | None:
    return _IDENTIFIER_KEY.get(identifier_type, "accession")


def usable_identifiers(dataset: dict, accession_types: list[str] | None = None) -> list[dict]:
    """The dataset's identifiers that can be matched against a given manifest.

    Confirmed and provisional identifiers count; an unconfirmed one is recorded in the
    catalog but must never let a finding claim that key was attempted (provisional ones
    make the finding's identity provisional — see identity_of). When the
    manifest declares `accession_types`, accessions of other kinds are not usable
    either: a GEO accession tried against CELLxGENE UUIDs is not an attempt.
    """
    usable = []
    for ident in dataset.get("identifiers", []):
        if ident.get("confirmed") not in (True, "provisional") or not identifier_key(ident.get("type", "")):
            continue
        if identifier_key(ident["type"]) == "accession" and accession_types is not None \
                and ident["type"] not in accession_types:
            continue
        usable.append(ident)
    return usable


def confirmed_keys(dataset: dict, accession_types: list[str] | None = None) -> set[str]:
    """Match keys this dataset can actually be searched on (see usable_identifiers)."""
    return {identifier_key(i["type"]) for i in usable_identifiers(dataset, accession_types)}


def provisional_keys(dataset: dict, accession_types: list[str] | None = None) -> tuple[set[str], tuple[str, ...]]:
    """Keys whose ONLY usable identifiers are provisional, and the bases of those identifiers.

    A finding that attempted any of these keys has a provisional identity: the search was
    real, but whether it searched for the right dataset rests on the stated basis.
    """
    by_key: dict[str, list[dict]] = {}
    for ident in usable_identifiers(dataset, accession_types):
        by_key.setdefault(identifier_key(ident["type"]), []).append(ident)
    keys = {k for k, idents in by_key.items() if all(i.get("confirmed") == "provisional" for i in idents)}
    bases = tuple(dict.fromkeys(i["basis"] for k in sorted(keys) for i in by_key[k] if i.get("basis")))
    return keys, bases


def identity_of(dataset: dict, keys_attempted, accession_types: list[str] | None = None) -> tuple[str | None, str | None]:
    """('provisional', basis) if any attempted key relies only on provisional identifiers, else (None, None)."""
    keys, bases = provisional_keys(dataset, accession_types)
    if keys & set(keys_attempted):
        return "provisional", " ".join(bases) or None
    return None, None


def relation(entry: dict, dataset_id: str) -> str:
    """'self-eval' if the model's own paper evaluated on this dataset, else 'catalog'.

    Derived, never authored — so it cannot disagree with evaluated_on.
    """
    own = {e.get("dataset") for e in entry.get("evaluated_on", [])}
    return "self-eval" if dataset_id in own else "catalog"
