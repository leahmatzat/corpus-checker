"""Dataset lookup: which models trained on this dataset? Is it in model X?

Starts from a dataset instead of a model. A query is any identifier — an accession, a
PMID, a DOI, a URL containing one, or a datasets/ id or name — expanded through the
crosswalk and matched against every model entry's manifest with the same rules as a
registry check (match.match -> verdict.decide).

Verification belongs to the model entries: a lookup asks questions of manifests a
person has already verified, so each answer carries that entry's verifier and date.
Only verified entries are searched unless include_drafts is set.

A PMID or DOI names a paper, not a dataset. A paper query is answered at the paper
level and, separately, for each dataset the paper links to — it never becomes a
dataset claim on its own (see verdict.PAPER_LEVEL_DISCLAIMER).
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path

from . import normalize
from .check import SourceError, locate_committed
from .crosswalk import CrosswalkError, resolve
from .ledger import build_ledger
from .match import RecordIndex, match
from .registry import EXACT_KEYS, confirmed_keys, identity_of, load_catalog, load_yaml, registry_paths
from .resolvers import get_resolver
from .verdict import PAPER_LEVEL_DISCLAIMER, decide

LOOKUP_INDEX_VERSION = 1

_PMID_INPUT_RE = re.compile(r"^(?:pmid:?\s*)?(\d{1,9})$", re.IGNORECASE)
_PUBMED_URL_RE = re.compile(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d{1,9})", re.IGNORECASE)
_UUID_RE = re.compile(r"^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
_NCBI_RESOLVABLE = re.compile(r"^(GS[EM]\d+|E-[A-Z]{4}-\d+|\d{1,9}|10\.\d{4,}/\S+)$", re.IGNORECASE)


def accession_type(token: str) -> str:
    """datasets/ identifier type for a canonical accession token."""
    t = token.upper()
    if _UUID_RE.match(token):
        return "cellxgene_dataset"   # collection vs dataset UUIDs share a format; both are searched
    for prefix, kind in (("GS", "geo"), ("E-", "arrayexpress"), ("PRJ", "bioproject"), ("EGA", "ega"),
                         ("SCP", "scp"), ("HRA", "gsa")):
        if t.startswith(prefix):
            return kind
    return "sra"


@dataclass(frozen=True)
class Query:
    raw: str
    kind: str                              # "dataset" | "paper" | "unrecognised"
    identifiers: tuple[tuple[str, str], ...]   # (type, value), what gets searched
    typed: tuple[str, ...]                 # what the user actually entered, canonicalised
    catalog_id: str | None = None
    linked_from: str | None = None         # for a dataset reached through a paper query
    notes: tuple[str, ...] = ()
    provisional: tuple[tuple[str, str, str], ...] = ()   # (type, value, basis) of provisional catalog identifiers


@dataclass(frozen=True)
class Answer:
    model: str
    model_name: str
    stage: str
    verdict: str
    code: str
    reason: str | None
    match_level: str | None
    keys_attempted: tuple[str, ...]
    matched_on: tuple[str, ...]
    samples: int | None
    cells: int | None
    sample_ids: tuple[str, ...]
    listed_accessions: tuple[str, ...]
    sources_searched: tuple[str, ...]
    recorded: bool                         # True = the registry's own finding for a catalog dataset
    identity: str | None                   # "provisional" when the dataset's identification is provisional
    identity_basis: str | None
    verified_by: str | None
    verified_date: str | None
    draft: bool


@dataclass(frozen=True)
class LookupResult:
    query: Query
    answers: tuple[Answer, ...]
    searched: tuple[str, ...]              # model ids searched — the coverage line
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- parsing + expansion

def parse(raw: str) -> dict[str, list[str]]:
    """Identifier tokens in one line of user input. PMIDs only from a bare number or a PubMed URL."""
    text = raw.strip()
    out: dict[str, list[str]] = {"accession": list(normalize.accessions(text)), "doi": list(normalize.dois(text)), "pmid": []}
    if m := _PMID_INPUT_RE.match(text):
        out["pmid"].append(m.group(1))
    out["pmid"] += _PUBMED_URL_RE.findall(text)
    return out


def _catalog_match(raw: str, accessions: set[str], catalog: dict[str, dict]) -> str | None:
    """A datasets/ entry, by id, by name, or by a shared confirmed ACCESSION (never by paper)."""
    key = raw.strip().lower()
    for d_id, d in catalog.items():
        if key in (d_id.lower(), str(d.get("name", "")).lower()):
            return d_id
    for d_id, d in sorted(catalog.items()):
        for ident in d.get("identifiers", []):
            if ident.get("confirmed") and normalize.accession(str(ident["value"])) in accessions:
                return d_id
    return None


def interpret(raw: str, catalog: dict[str, dict], *, network: bool = True,
              cache_dir: Path | None = None) -> list[Query]:
    """One line of input -> one or more queries (a paper expands to its linked datasets)."""
    catalog_id = _catalog_match(raw, set(), catalog)
    if catalog_id:
        d = catalog[catalog_id]
        idents = tuple((i["type"], str(i["value"])) for i in d["identifiers"] if i.get("confirmed") in (True, "provisional"))
        prov = tuple((i["type"], str(i["value"]), i.get("basis", "")) for i in d["identifiers"] if i.get("confirmed") == "provisional")
        return [Query(raw, "dataset", idents, (catalog_id,), catalog_id=catalog_id, provisional=prov)]

    tokens = parse(raw)
    typed = tuple(tokens["accession"] + tokens["pmid"] + tokens["doi"])
    if not typed:
        return [Query(raw, "unrecognised", (), (), notes=("No accession, PMID or DOI found in this input.",))]

    notes: list[str] = []
    accessions, pmids, dois, linked = set(tokens["accession"]), set(tokens["pmid"]), set(tokens["doi"]), set()
    for token in typed:
        if not _NCBI_RESOLVABLE.match(token):
            continue   # CELLxGENE UUIDs, SRA/BioProject/EGA ids: searched as entered
        try:
            rec = resolve(token, network=network, cache_dir=cache_dir)
        except CrosswalkError as exc:
            notes.append(f"Could not expand {token} through NCBI ({exc.__class__.__name__}); matched only on what was entered.")
            continue
        pmids.update(rec.pmids)
        if rec.doi:
            dois.add(normalize.doi(rec.doi))
        if token.upper().startswith("GSE"):
            accessions.update(rec.accessions)          # the same series' BioProject / SRA ids
        elif token in tokens["pmid"] or token in tokens["doi"]:
            linked.update(a for a in rec.accessions if a.upper().startswith("GSE"))
        # A GSM is one sample: never widen it to its series, or sibling samples would match.

    if tokens["accession"]:
        catalog_id = _catalog_match(raw, accessions, catalog)
        if catalog_id:   # a typed accession that is a catalog identifier: use the catalog's view of it
            return [Query(raw, q.kind, q.identifiers, typed, catalog_id, notes=tuple(notes), provisional=q.provisional)
                    for q in interpret(catalog_id, catalog, network=False)]
        idents = [(accession_type(a), a) for a in sorted(accessions)] + \
                 [("pmid", p) for p in sorted(pmids)] + [("doi", d) for d in sorted(dois)]
        return [Query(raw, "dataset", tuple(idents), typed, notes=tuple(notes))]

    # A paper: answer at the paper level, then once per dataset it links to.
    paper = Query(raw, "paper", tuple([("pmid", p) for p in sorted(pmids)] + [("doi", d) for d in sorted(dois)]),
                  typed, notes=tuple(notes))
    queries = [paper]
    for gse in sorted(linked):
        queries += [Query(gse, q.kind, q.identifiers, q.typed, q.catalog_id, linked_from=raw, notes=q.notes,
                          provisional=q.provisional)
                    for q in interpret(gse, catalog, network=network, cache_dir=cache_dir)]
    return queries


# --------------------------------------------------------------------------- searching

@dataclass
class _Corpus:
    model: str
    model_name: str
    stage: str
    manifest: dict
    sources_searched: tuple[str, ...]
    verified_by: str | None
    verified_date: str | None
    draft: bool
    index: RecordIndex | None = None
    can_prove_presence: bool = False
    unavailable: str | None = None
    unconfirmed: tuple[str, ...] = ()


def load_corpora(root: Path, *, include_drafts: bool = False, models: list[str] | None = None,
                 manifests_dir: Path | None = None) -> list[_Corpus]:
    from .check import locate_in_dir

    fallback = locate_in_dir(manifests_dir) if manifests_dir else None
    out = []
    for path in registry_paths(root):
        e = load_yaml(path)
        prov = e["provenance"]
        draft = not prov["verified_by"].strip()
        if (draft and not include_drafts) or (models and e["id"] not in models):
            continue
        for c in e["corpora"]:
            m = c["manifest"]
            corpus = _Corpus(e["id"], e["model"], c["stage"], m, tuple(c["result"]["sources_searched"]),
                             prov["verified_by"] or None, prov["verified_date"], draft)
            if m["type"] in ("C-vague", "D"):
                out.append(corpus)
                continue
            try:
                resolver = get_resolver(m["resolver"])
            except ValueError:
                corpus.unavailable = f"no resolver for {m['resolver']!r} manifests yet"
                out.append(corpus)
                continue
            locate = locate_committed(root, fallback)
            try:
                records = [r for s in m.get("sources", []) for r in resolver.records(s, locate(s))]
            except SourceError as exc:
                corpus.unavailable = str(exc)
            else:
                corpus.index = RecordIndex(records)
            corpus.can_prove_presence = resolver.can_prove_presence
            corpus.unconfirmed = tuple(s["name"] for s in m.get("sources", []) if s["confirmation"]["method"] == "none")
            out.append(corpus)
    return out


def _answer(corpus: _Corpus, query: Query) -> Answer:
    base = dict(model=corpus.model, model_name=corpus.model_name, stage=corpus.stage,
                sources_searched=corpus.sources_searched, verified_by=corpus.verified_by,
                verified_date=corpus.verified_date, draft=corpus.draft, recorded=False)
    unknown_identity = dict(identity=None, identity_basis=None)
    m = corpus.manifest
    if m["type"] in ("C-vague", "D"):
        d = decide(manifest_type=m["type"], can_prove_presence=False, match=None)
        return Answer(**base, **unknown_identity, verdict=d.verdict, code=d.code, reason=d.reason, match_level=None,
                      keys_attempted=(), matched_on=(), samples=None, cells=None, sample_ids=(), listed_accessions=())
    if corpus.index is None:
        return Answer(**base, **unknown_identity, verdict="INCONCLUSIVE", code="not_indexed",
                      reason=f"this manifest is not available to search here ({corpus.unavailable})",
                      match_level=None, keys_attempted=(), matched_on=(), samples=None, cells=None,
                      sample_ids=(), listed_accessions=())

    provisional = {(t, v): basis for t, v, basis in query.provisional}
    dataset = {"id": "query", "identifiers": [
        {"type": t, "value": v, "confirmed": "provisional", "basis": provisional[(t, v)]} if (t, v) in provisional
        else {"type": t, "value": v, "confirmed": True}
        for t, v in query.identifiers]}
    accession_types = m.get("accession_types")
    if accession_types and "cellxgene_collection" in accession_types:
        # A UUID typed by the user may be a collection or a dataset id; offer it as both.
        extra = [{**i, "type": "cellxgene_collection"} for i in dataset["identifiers"] if i["type"] == "cellxgene_dataset"]
        dataset["identifiers"] += extra
    keys = sorted(confirmed_keys(dataset, accession_types) & set(m.get("keys_available", [])) & EXACT_KEYS)
    mt = match(corpus.index, dataset, keys, accession_types)
    d = decide(manifest_type=m["type"], can_prove_presence=corpus.can_prove_presence, match=mt,
               requires_keys=frozenset(m.get("requires_keys", [])), unconfirmed_sources=corpus.unconfirmed)
    identity, basis = identity_of(dataset, keys, accession_types)
    return Answer(**base, verdict=d.verdict, code=d.code, reason=d.reason, match_level=mt.match_level,
                  identity=identity, identity_basis=basis,
                  keys_attempted=tuple(keys), matched_on=mt.keys_hit,
                  samples=len(mt.sample_ids) or None, cells=mt.cells, sample_ids=mt.sample_ids,
                  listed_accessions=mt.listed_accessions)


def _recorded(corpus: _Corpus, finding: dict) -> Answer:
    return Answer(model=corpus.model, model_name=corpus.model_name, stage=corpus.stage,
                  verdict=finding["verdict"], code="recorded", reason=finding.get("reason"),
                  match_level=finding.get("match_level"),
                  keys_attempted=tuple(finding.get("keys_attempted", [])), matched_on=tuple(finding.get("matched_on", [])),
                  samples=finding.get("samples"), cells=finding.get("cells"),
                  sample_ids=tuple(finding.get("sample_ids", [])), listed_accessions=(),
                  sources_searched=corpus.sources_searched, recorded=True,
                  identity=finding.get("identity"), identity_basis=finding.get("identity_basis"),
                  verified_by=corpus.verified_by, verified_date=corpus.verified_date, draft=corpus.draft)


def search(query: Query, corpora: list[_Corpus], recorded_rows: list[dict]) -> LookupResult:
    """Answer one query against every loaded corpus. Recorded findings win for catalog datasets."""
    answers = []
    for c in corpora if query.kind != "unrecognised" else []:
        rec = next((r for r in recorded_rows if query.catalog_id and r["dataset"] == query.catalog_id
                    and r["model"] == c.model and r["stage"] == c.stage), None)
        answers.append(_recorded(c, rec) if rec else _answer(c, query))
    warnings = []
    if query.kind == "paper" or any(a.match_level == "publication" for a in answers):
        warnings.append(PAPER_LEVEL_DISCLAIMER)
    return LookupResult(query, tuple(answers), tuple(dict.fromkeys(c.model for c in corpora)), tuple(warnings))


def lookup(inputs: list[str], root: Path, *, network: bool = True, models: list[str] | None = None,
           include_drafts: bool = False, manifests_dir: Path | None = None,
           cache_dir: Path | None = None) -> list[LookupResult]:
    catalog = load_catalog(root)
    corpora = load_corpora(root, include_drafts=include_drafts, models=models, manifests_dir=manifests_dir)
    recorded = build_ledger(root)["rows"]
    return [search(q, corpora, recorded)
            for raw in inputs if raw.strip()
            for q in interpret(raw, catalog, network=network, cache_dir=cache_dir)]


# --------------------------------------------------------------------------- plain-language answers

_SHORT = {"PRESENT": "Yes", "NOT PRESENT": "No", "INCONCLUSIVE": "Can't tell", "NOT CHECKABLE": "Unknown"}


def answer_text(a: Answer, *, lead: bool = True) -> str:
    """One plain sentence per answer. lead=False drops the leading Yes/No/… for tables that show it."""
    where = f"{a.model_name}'s {a.stage} data"
    name = lambda k: k.upper() if k in ("pmid", "doi") else k
    if a.verdict == "PRESENT":
        size = ", ".join(x for x in (f"{a.samples} samples" if a.samples else "",
                                     f"{a.cells:,} cells" if a.cells is not None else "") if x)
        keys = " and ".join(name(k) for k in a.matched_on)
        body = f"in {where}" + (f": {size}" if size else "") + (f" (matched on {keys})" if keys else "") + "."
    elif a.verdict == "NOT PRESENT":
        body = (f"not in {where}; searched {', '.join(a.sources_searched)} "
                f"on {' and '.join(name(k) for k in a.keys_attempted)}.")
    elif a.verdict == "NOT CHECKABLE":
        body = f"{a.model_name} did not publish a training-data list for its {a.stage} stage."
    else:
        body = f"{a.reason.rstrip('.')}." if a.reason else ""
    if a.identity == "provisional":
        body += f" (provisional identity: {a.identity_basis.rstrip('.') if a.identity_basis else 'basis not recorded'}.)"
    return f"{short(a.verdict)} — {body}" if lead else body


def identifier_label(kind: str, value: str) -> str:
    return {"pmid": f"PMID {value}", "doi": f"DOI {value}"}.get(kind, value)


def short(verdict: str) -> str:
    return _SHORT.get(verdict, verdict)


def provenance_text(a: Answer) -> str:
    if a.draft:
        return "draft entry — not yet verified"
    return f"manifest recorded by {a.verified_by}, {a.verified_date}"


# --------------------------------------------------------------------------- the web index

def build_lookup_index(root: Path, *, include_drafts: bool = False) -> dict:
    """Everything the site's lookup page needs to answer a query in the browser, with no server.

    Rows are compact arrays: [source_index, project, sample, cells, accessions, pmids, dois].
    The browser rebuilds its token maps from them and applies the same rules as
    verdict.decide_from() — tests/fixtures/lookup_vectors.json holds the parity cases.
    Deterministic: no timestamps, stable ordering.
    """
    corpora_out = []
    for c in load_corpora(root, include_drafts=include_drafts):
        m = c.manifest
        sources = [s["name"] for s in m.get("sources", [])]
        rows = []
        if c.index is not None:
            for r in c.index.records:
                rows.append([sources.index(r.source) if r.source in sources else -1, r.project, r.sample,
                             r.cells, list(r.accessions), list(r.pmids), list(r.dois)])
        corpora_out.append({
            "model": c.model, "model_name": c.model_name, "stage": c.stage,
            "manifest_type": m["type"], "checkable": m["type"] not in ("C-vague", "D"),
            "indexed": c.index is not None, "unavailable": c.unavailable,
            "can_prove_presence": c.can_prove_presence,
            "keys_available": sorted(m.get("keys_available", [])), "requires_keys": sorted(m.get("requires_keys", [])),
            "accession_types": m.get("accession_types"),
            "sources": sources, "sources_searched": list(c.sources_searched),
            "unconfirmed_sources": list(c.unconfirmed),
            "verified_by": c.verified_by, "verified_date": c.verified_date, "draft": c.draft,
            "rows": rows,
        })
    # Catalog identifiers: [type, value] when confirmed, [type, value, "provisional", basis] when provisional.
    catalog = {d_id: {"name": d["name"],
                      "identifiers": [[i["type"], str(i["value"])] if i["confirmed"] is True
                                      else [i["type"], str(i["value"]), "provisional", i.get("basis", "")]
                                      for i in d["identifiers"] if i.get("confirmed") in (True, "provisional")]}
               for d_id, d in sorted(load_catalog(root).items())}
    models = {c["model"] for c in corpora_out}
    recorded = [r for r in build_ledger(root)["rows"] if r["model"] in models]
    return {"index_version": LOOKUP_INDEX_VERSION, "disclaimer": PAPER_LEVEL_DISCLAIMER,
            "corpora": corpora_out, "catalog": catalog, "recorded": recorded}
