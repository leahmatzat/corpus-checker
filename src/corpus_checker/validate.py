"""Registry validation: JSON Schema plus the cross-field guards schema cannot express.

Every guard has a stable code. Codes are listed in docs/RULES.md and in the
tests, so a guard cannot be dropped without a visible diff.

    G00  schema violation (registry/ or datasets/)
    G01  exact-key rule: title/keyword-only matches cannot yield PRESENT or NOT PRESENT
    G02  finding or evaluated_on points at a dataset not in datasets/
    G03  verdict-bearing content changed vs --base-ref but provenance was not re-signed
    G04  re-fetched manifest source no longer matches its sha256 (--check-sources)
    G05  manifest as_of older than --stale-months                              [warning]
    G06  canary regression — lives in tests/test_canary.py, not here
    G07  sources_searched names a source the manifest does not list
    G08  NOT PRESENT from a manifest whose sources were not all searched
    G09  PRESENT / NOT PRESENT from a manifest with an unconfirmed source
    G10  verified_by empty, or not a person listed in verifiers.yaml
    G11  NOT CHECKABLE without every discovery location checked
    G12  id does not match file name, or is duplicated
    G13  keys_attempted / matched_on outside manifest.keys_available, or matched ⊄ attempted
    G14  NOT PRESENT without every required key attempted
    G15  an attempted exact key has no confirmed identifier in datasets/
    G16  the same dataset appears twice in one corpus's findings
    G17  a reported-eval dataset has no finding in any corpus                  [warning]
    G18  NOT PRESENT while discovery is incomplete                             [warning]
    G19  a manifest source's roles name a column not in its `columns`
    G20  a committed snapshot is missing or no longer matches its sha256
    G21  PRESENT on a publication-level match (PMID/DOI only) without a verifier's identity_note
    G22  a committed extract is missing or does not declare its source's sha256
    G23  a finding's identity (provisional or not) disagrees with the catalog identifiers it attempted
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import subprocess
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable

from jsonschema import Draft202012Validator

from .check import extract_parent_sha256
from .registry import (
    EXACT_KEYS,
    WEAK_KEYS,
    confirmed_keys,
    identity_of,
    load_catalog,
    load_schema,
    load_verifiers,
    load_yaml,
    load_yaml_text,
)

ERROR = "error"
WARNING = "warning"


@dataclass(frozen=True)
class Issue:
    code: str
    severity: str
    path: str
    where: str
    message: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Options:
    allow_draft: bool = False
    stale_months: int = 12
    today: dt.date | None = None
    base_ref: str | None = None
    check_sources: bool = False
    fetch: Callable[[str], bytes] | None = None   # injectable for tests


def _validator(root: Path, name: str) -> Draft202012Validator:
    return Draft202012Validator(load_schema(root, name), format_checker=Draft202012Validator.FORMAT_CHECKER)


def _pointer(parts: Iterable) -> str:
    out = ""
    for p in parts:
        out += f"[{p}]" if isinstance(p, int) else (f".{p}" if out else str(p))
    return out or "(root)"


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


# --------------------------------------------------------------------------- datasets/

def check_dataset(path: Path, doc: object, root: Path) -> list[Issue]:
    rel = _rel(path, root)
    issues = [Issue("G00", ERROR, rel, _pointer(e.absolute_path), e.message)
              for e in _validator(root, "dataset.schema.json").iter_errors(doc)]
    if not issues and doc.get("id") != path.stem:
        issues.append(Issue("G12", ERROR, rel, "id", f"id {doc.get('id')!r} must equal the file name stem {path.stem!r}"))
    return issues


# --------------------------------------------------------------------------- registry/

def check_entry(path: Path, doc: object, root: Path, catalog: dict[str, dict],
                verifiers: set[str], opts: Options) -> list[Issue]:
    rel = _rel(path, root)
    issues: list[Issue] = []

    def add(code: str, where: str, message: str, severity: str = ERROR) -> None:
        issues.append(Issue(code, severity, rel, where, message))

    schema_errors = list(_validator(root, "registry.schema.json").iter_errors(doc))
    for e in sorted(schema_errors, key=lambda e: list(map(str, e.absolute_path))):
        add("G00", _pointer(e.absolute_path), e.message)
    if schema_errors:
        return issues   # the cross-field rules below assume a well-formed entry

    if doc["id"] != path.stem:
        add("G12", "id", f"id {doc['id']!r} must equal the file name stem {path.stem!r}")

    # G10 — a person looked
    prov = doc["provenance"]
    signer = prov["verified_by"].strip()
    if not signer:
        add("G10", "provenance.verified_by",
            "empty: drafted, not verified. A verifier must fill this before merge.",
            WARNING if opts.allow_draft else ERROR)
    elif signer not in verifiers:
        add("G10", "provenance.verified_by", f"{signer!r} is not listed in verifiers.yaml")

    discovery = doc["discovery"]
    unchecked = sorted(k for k, item in discovery.items() if not item["checked"])

    for i, ev in enumerate(doc["evaluated_on"]):
        if ev["dataset"] not in catalog:
            add("G02", f"evaluated_on[{i}].dataset", f"{ev['dataset']!r} is not in datasets/")

    found_anywhere: set[str] = set()
    any_not_present = False
    for ci, corpus in enumerate(doc["corpora"]):
        manifest, result = corpus["manifest"], corpus["result"]
        mtype = manifest["type"]
        sources = manifest.get("sources", [])
        source_names = [s["name"] for s in sources]
        searched = result["sources_searched"]
        keys_available = set(manifest.get("keys_available", []))
        requires = set(manifest.get("requires_keys", []))
        unconfirmed = [s["name"] for s in sources if s["confirmation"]["method"] == "none"]

        # G05 — staleness is information, not an error
        if opts.stale_months and (age := _months_since(manifest["as_of"], opts.today)) is not None \
                and age > opts.stale_months:
            add("G05", f"corpora[{ci}].manifest.as_of",
                f"as_of {manifest['as_of']} is {age} months old (threshold {opts.stale_months})", WARNING)

        # G20 — a committed snapshot is in the repo, so its hash is always checkable
        for si, src in enumerate(sources):
            if snap := src.get("snapshot"):
                snap_path = root / snap
                if not snap_path.is_file():
                    add("G20", f"corpora[{ci}].manifest.sources[{si}].snapshot", f"{snap} does not exist")
                elif (digest := hashlib.sha256(snap_path.read_bytes()).hexdigest()) != src["sha256"]:
                    add("G20", f"corpora[{ci}].manifest.sources[{si}].snapshot",
                        f"{snap} has sha256 {digest[:12]}…, the registry recorded {src['sha256'][:12]}…")

        # G22 — an extract stands in for a publisher file only if it says which one
        for si, src in enumerate(sources):
            if ext := src.get("extract"):
                ext_path = root / ext
                if not ext_path.is_file():
                    add("G22", f"corpora[{ci}].manifest.sources[{si}].extract", f"{ext} does not exist")
                elif extract_parent_sha256(ext_path) != src["sha256"]:
                    add("G22", f"corpora[{ci}].manifest.sources[{si}].extract",
                        f"{ext} does not declare derived-from-sha256: {src['sha256'][:12]}…")

        # G19 — a role pointing at a missing column would silently read nothing
        for si, src in enumerate(sources):
            if "columns" in src and "roles" in src:
                named = [c for role, cols in src["roles"].items() for c in (cols if isinstance(cols, list) else [cols])]
                if missing_cols := [c for c in named if c not in src["columns"]]:
                    add("G19", f"corpora[{ci}].manifest.sources[{si}].roles",
                        f"roles name columns {missing_cols} that are not in columns {src['columns']}")

        # G07 — every searched source must be a real source (A / A+ list their files)
        if mtype in ("A", "A+"):
            for name in searched:
                if name not in source_names:
                    add("G07", f"corpora[{ci}].result.sources_searched",
                        f"{name!r} is not one of manifest.sources: {source_names}")

        seen: set[str] = set()
        for fi, f in enumerate(result["findings"]):
            ds, verdict = f["dataset"], f["verdict"]
            where = f"corpora[{ci}].result.findings[{fi}] ({ds})"
            attempted = set(f.get("keys_attempted", []))
            matched = set(f.get("matched_on", []))

            if ds in seen:
                add("G16", where, f"{ds!r} appears more than once in this corpus's findings")
            seen.add(ds)
            found_anywhere.add(ds)

            if ds not in catalog:
                add("G02", where, f"{ds!r} is not in datasets/")
                continue

            # G01 — exact-key rule
            if verdict == "PRESENT" and not matched & EXACT_KEYS:
                add("G01", where, f"PRESENT requires an exact key (accession/pmid/doi) in matched_on; got {sorted(matched)}")
            if verdict in ("PRESENT", "NOT PRESENT") and attempted and not attempted & EXACT_KEYS:
                add("G01", where, f"{verdict} requires an exact key attempt; only {sorted(attempted)} were tried — cap at INCONCLUSIVE")
            if matched and matched <= WEAK_KEYS and verdict != "INCONCLUSIVE":
                add("G01", where, f"a {'/'.join(sorted(matched))}-only match must be INCONCLUSIVE, not {verdict}")

            # G13 — keys must be ones this manifest can be searched on
            if keys_available and (outside := (attempted | matched) - keys_available):
                add("G13", where, f"keys {sorted(outside)} are not in manifest.keys_available {sorted(keys_available)}")
            if not matched <= attempted:
                add("G13", where, f"matched_on {sorted(matched)} must be a subset of keys_attempted {sorted(attempted)}")

            # G15 — an attempted exact key must be backed by a confirmed identifier
            backed = confirmed_keys(catalog[ds], manifest.get("accession_types"))
            for key in sorted(attempted & EXACT_KEYS - backed):
                add("G15", where, f"keys_attempted includes {key!r} but datasets/{ds}.yaml has no confirmed identifier of that kind")

            if verdict == "NOT PRESENT":
                # G14 — dual-key rule, mechanically
                if missing := requires - attempted:
                    add("G14", where,
                        f"NOT PRESENT against this manifest requires keys {sorted(requires)}; not attempted: {sorted(missing)}")
                if skipped := (backed & keys_available & EXACT_KEYS) - attempted:
                    add("G14", where,
                        f"the dataset has confirmed {sorted(skipped)} identifiers this manifest can match on, but they were not attempted")
                # G08 — a NOT PRESENT from one of two files is not a NOT PRESENT
                if mtype in ("A", "A+") and (missing_src := [n for n in source_names if n not in searched]):
                    add("G08", where, f"NOT PRESENT but these manifest sources were not searched: {missing_src}")
                any_not_present = True

            # G21 — a PMID or DOI names a paper, not a dataset
            if verdict == "PRESENT" and not f.get("identity_note"):
                if f.get("match_level") == "publication":
                    add("G21", where, "PRESENT on a publication-level match (PMID/DOI only) needs an identity_note "
                                      "from a verifier saying why it is the same dataset — otherwise INCONCLUSIVE")
                elif f.get("match_level") == "dataset" and "accession" not in matched:
                    add("G21", where, "match_level: dataset requires an accession in matched_on (or an identity_note)")

            # G23 — identity is derived from the catalog, so it cannot disagree with it
            derived, _ = identity_of(catalog[ds], attempted, manifest.get("accession_types"))
            if derived != f.get("identity"):
                add("G23", where, f"identity should be {derived or 'confirmed (omitted)'} given the catalog identifiers "
                                  f"behind keys_attempted {sorted(attempted)}; recorded {f.get('identity') or 'confirmed'}")

            # G09 — an unconfirmed file cannot support a definite verdict
            if unconfirmed and verdict in ("PRESENT", "NOT PRESENT"):
                add("G09", where, f"{verdict} rests on unconfirmed manifest source(s) {unconfirmed}; cap at INCONCLUSIVE")

            # G11 — "the paper did not publish enough" is a public claim; prove we looked
            if verdict == "NOT CHECKABLE" and unchecked:
                add("G11", where, f"NOT CHECKABLE requires every discovery location checked; unchecked: {unchecked}")

    if any_not_present and unchecked:
        add("G18", "discovery", f"NOT PRESENT verdicts while these discovery locations are unchecked: {unchecked}", WARNING)

    for i, ev in enumerate(doc["evaluated_on"]):
        if ev["dataset"] in catalog and ev["dataset"] not in found_anywhere:
            add("G17", f"evaluated_on[{i}]", f"reported-eval dataset {ev['dataset']!r} has no finding in any corpus", WARNING)

    if opts.check_sources:
        issues.extend(_check_sources(rel, doc, opts))

    return issues


def _months_since(date_str: str, today: dt.date | None) -> int | None:
    try:
        then = dt.date.fromisoformat(date_str)
    except ValueError:
        return None
    now = today or dt.date.today()
    return (now.year - then.year) * 12 + (now.month - then.month)


# --------------------------------------------------------------------------- G04

def _default_fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "corpus-checker (+https://github.com/leahmatzat/corpus-checker)"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def _check_sources(rel: str, doc: dict, opts: Options) -> list[Issue]:
    """Re-fetch every manifest source with a URL and compare hashes.

    A mismatch is an error: the manifest moved. An unreachable URL is only a warning —
    a network failure is not drift, and publisher sites often block CI runners.
    """
    fetch = opts.fetch or _default_fetch
    issues = []
    for ci, corpus in enumerate(doc["corpora"]):
        for si, src in enumerate(corpus["manifest"].get("sources", [])):
            if src.get("snapshot") or src["confirmation"]["method"] == "api_snapshot":
                continue   # not re-fetchable bytes; G20 checks the committed file, the snapshot script re-derives it
            where = f"corpora[{ci}].manifest.sources[{si}] ({src['name']})"
            try:
                digest = hashlib.sha256(fetch(src["url"])).hexdigest()
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                issues.append(Issue("G04", WARNING, rel, where, f"could not fetch {src['url']}: {exc}"))
                continue
            if digest != src["sha256"]:
                issues.append(Issue("G04", ERROR, rel, where,
                                    f"sha256 of the file now served ({digest[:12]}…) differs from the recorded {src['sha256'][:12]}…"))
            elif src["acquired"] == "supplied" and src["confirmation"]["method"] != "url_hash":
                issues.append(Issue("G04", WARNING, rel, where,
                                    "the URL now serves exactly the supplied file — a verifier can upgrade confirmation to url_hash"))
    return issues


# --------------------------------------------------------------------------- G03

def _verdict_bearing(doc: dict) -> str:
    """Everything a verdict depends on. Changing any of it requires a re-sign.

    Schema v1 kept one manifest/result pair at the top level; v2 nests them under
    corpora. Both are normalised so a v1 -> v2 migration also counts as a change.
    """
    if doc.get("schema_version") == 2:
        payload = {"corpora": doc.get("corpora"), "evaluated_on": doc.get("evaluated_on")}
    else:
        payload = {"manifest": doc.get("manifest"), "result": doc.get("result"),
                   "evaluation_datasets": doc.get("evaluation_datasets")}
    return json.dumps(payload, sort_keys=True, default=str)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


def base_versions(root: Path, base_ref: str) -> dict[str, str | None]:
    """Map each current registry path to its path at base_ref, following renames.

    None means the file is new since base_ref.
    """
    mapping: dict[str, str | None] = {}
    out = _git(root, "diff", "--name-status", "-M", base_ref, "--", "registry/")
    for line in out.splitlines():
        status, *names = line.split("\t")
        if status.startswith("R"):
            mapping[names[1]] = names[0]
        elif status.startswith("A"):
            mapping[names[0]] = None
        elif status.startswith(("M", "T")):
            mapping[names[0]] = names[0]
    return mapping


def check_base_ref(root: Path, paths: list[Path], base_ref: str) -> list[Issue]:
    issues = []
    try:
        mapping = base_versions(root, base_ref)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"git could not diff against {base_ref!r}: {exc.stderr.strip()}") from exc
    for path in paths:
        rel = _rel(path, root)
        if rel not in mapping or mapping[rel] is None:
            continue   # unchanged, or new (a new entry is covered by G10)
        head = load_yaml(path)
        base = load_yaml_text(_git(root, "show", f"{base_ref}:{mapping[rel]}"))
        if not isinstance(head, dict) or not isinstance(base, dict):
            continue
        if _verdict_bearing(head) == _verdict_bearing(base):
            continue
        hp, bp = head.get("provenance", {}), base.get("provenance", {})
        if not hp.get("verified_by", "").strip():
            continue   # already unsigned; G10 blocks it
        if (hp.get("verified_by"), hp.get("verified_date")) == (bp.get("verified_by"), bp.get("verified_date")):
            issues.append(Issue("G03", ERROR, rel, "provenance",
                                f"verdict-bearing content changed since {base_ref} but provenance still carries "
                                f"{hp.get('verified_by')} / {hp.get('verified_date')}. A verifier must re-sign "
                                f"(update verified_date) after reviewing the change."))
    return issues


# --------------------------------------------------------------------------- entry point

def validate(root: Path, paths: list[Path] | None = None, opts: Options | None = None) -> list[Issue]:
    opts = opts or Options()
    catalog = load_catalog(root)
    verifiers = load_verifiers(root)
    issues: list[Issue] = []

    if paths is None:
        entry_paths = sorted((root / "registry").glob("*.yaml"))
        data_paths = sorted((root / "datasets").glob("*.yaml"))
    else:
        entry_paths = [p for p in paths if p.parent.name == "registry"]
        data_paths = [p for p in paths if p.parent.name == "datasets"]

    for path in data_paths:
        issues.extend(check_dataset(path, load_yaml(path), root))

    ids: dict[str, str] = {}
    for path in entry_paths:
        doc = load_yaml(path)
        issues.extend(check_entry(path, doc, root, catalog, verifiers, opts))
        if isinstance(doc, dict) and isinstance(doc.get("id"), str):
            if doc["id"] in ids:
                issues.append(Issue("G12", ERROR, _rel(path, root), "id", f"id {doc['id']!r} is also used by {ids[doc['id']]}"))
            ids[doc["id"]] = _rel(path, root)

    if opts.base_ref:
        issues.extend(check_base_ref(root, entry_paths, opts.base_ref))

    return issues
