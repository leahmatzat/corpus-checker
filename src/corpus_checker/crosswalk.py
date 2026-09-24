"""The crosswalk: resolve a dataset identifier to its canonical triple through NCBI.

`resolve()` takes a GEO accession, a PMID, or a DOI and returns the other two plus
sample-level detail, by walking NCBI eUtils (esearch/esummary/elink):

    GSE133344 -> esearch db=gds term=GSE133344[ACCN] -> uid 200133344
              -> esummary db=gds (entrytype == "GSE") -> pubmedids ["31395745"], samples, taxon
              -> esummary db=pubmed id=31395745 -> articleids -> doi, pmcid

Every result is cached to `crosswalk/cache/<kind>/<id>.json`, COMMITTED to git, so
the tool works with `--no-network` from the checkout alone. Cache
is always read first; network is the fallback a cache miss falls through to, never
the default path.

`match_citation`/`parse_citation` are the citation crosswalk (Geneformer's
manifest is keyed by citation strings, not accessions) but live here because they
share the cache, the rate limiter and the eUtils client.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from .registry import repo_root

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
BIOSTUDIES_BASE = "https://www.ebi.ac.uk/biostudies/api/v1/studies/"


class CrosswalkError(Exception):
    """A crosswalk lookup could not be completed."""


class CacheMiss(CrosswalkError):
    """`network=False` and no cached record exists for this identifier."""


@dataclass(frozen=True)
class CrosswalkRecord:
    """One identifier's canonical triple, plus what esummary gives for free.

    `samples` is (GEO sample accession, sample title) — a better
    perturbation detector than grepping study titles, because the titles say things
    like "sgRNA perturb-seq experiment" that a study-level title never mentions.
    """

    query: str                                   # normalised input, e.g. "GSE133344" or a lowercased DOI
    accessions: tuple[str, ...] = ()              # GEO GSE/GSM, BioProject, SRA study/experiment, ...
    pmids: tuple[str, ...] = ()
    doi: str | None = None
    pmcid: str | None = None
    title: str | None = None
    organism: str | None = None
    samples: tuple[tuple[str, str], ...] = ()
    retrieved: str = ""                           # ISO date — staleness must be visible
    sources: tuple[str, ...] = ()                 # which API calls produced this record


def as_dict(record: CrosswalkRecord) -> dict:
    """The JSON shape written to cache and printed by `corpus-checker resolve --format json`."""
    return {
        "query": record.query,
        "accessions": list(record.accessions),
        "pmids": list(record.pmids),
        "doi": record.doi,
        "pmcid": record.pmcid,
        "title": record.title,
        "organism": record.organism,
        "samples": [list(s) for s in record.samples],
        "retrieved": record.retrieved,
        "sources": list(record.sources),
    }


def _record_from_dict(doc: dict) -> CrosswalkRecord:
    return CrosswalkRecord(
        query=doc["query"],
        accessions=tuple(doc.get("accessions", [])),
        pmids=tuple(doc.get("pmids", [])),
        doi=doc.get("doi"),
        pmcid=doc.get("pmcid"),
        title=doc.get("title"),
        organism=doc.get("organism"),
        samples=tuple(tuple(s) for s in doc.get("samples", [])),
        retrieved=doc.get("retrieved", ""),
        sources=tuple(doc.get("sources", [])),
    )


def _today() -> str:
    return dt.date.today().isoformat()


# --------------------------------------------------------------------------- identifier normalisation

_GEO_RE = re.compile(r"^GS[EM]\d+$", re.I)
_PMID_RE = re.compile(r"^\d+$")
_DOI_RE = re.compile(r"^10\.\d{4,}/\S+$", re.I)
_ARRAYEXPRESS_RE = re.compile(r"^E-[A-Z]{4}-\d+$", re.I)


def _classify(identifier: str) -> tuple[str, str]:
    """(kind, normalised value). kind is a cache directory name: geo, pubmed, doi, arrayexpress."""
    s = identifier.strip()
    if _GEO_RE.match(s):
        return "geo", s.upper()
    if _ARRAYEXPRESS_RE.match(s):
        return "arrayexpress", s.upper()
    if _DOI_RE.match(s):
        return "doi", s.lower()
    if _PMID_RE.match(s):
        return "pubmed", s
    raise CrosswalkError(f"unrecognised identifier {identifier!r}: expected GSE…, GSM…, a PMID, or a DOI (10.…)")


def _encode_doi(doi: str) -> str:
    """A DOI cache filename: lowercase, '/' made reversible.

    DOI suffixes may legally contain '_', so escape a literal one first (to '~5f')
    before using '_' as the '/' placeholder — decode() then round-trips exactly.
    """
    return doi.strip().lower().replace("_", "~5f").replace("/", "_")


def _decode_doi(name: str) -> str:
    return name.replace("_", "/").replace("~5f", "_")


def _cache_filename(kind: str, value: str) -> str:
    return _encode_doi(value) if kind == "doi" else value


# --------------------------------------------------------------------------- cache

def _cache_path(cache_dir: Path, kind: str, value: str) -> Path:
    return cache_dir / kind / f"{_cache_filename(kind, value)}.json"


def _read_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_cache(cache_dir: Path, kind: str, value: str) -> CrosswalkRecord | None:
    doc = _read_json(_cache_path(cache_dir, kind, value))
    return None if doc is None else _record_from_dict(doc)


def _write_cache(cache_dir: Path, kind: str, value: str, record: CrosswalkRecord) -> None:
    _write_json(_cache_path(cache_dir, kind, value), as_dict(record))


# --------------------------------------------------------------------------- rate limiting + HTTP

def _real_clock() -> float:
    return time.monotonic()


def _real_sleep(seconds: float) -> None:
    time.sleep(seconds)


class RateLimiter:
    """Spaces `.wait()` calls at least `1/per_second` apart.

    `clock`/`sleep` are injectable (constructor keywords) so a test can verify the
    spacing arithmetic with a fake clock and never really sleep. The defaults call
    through the module's `time` functions indirectly, so monkeypatching
    `corpus_checker.crosswalk.time.sleep` also disables real sleeping for callers
    that use the module-level shared limiter.
    """

    def __init__(self, per_second: float, *,
                 clock: Callable[[], float] = _real_clock,
                 sleep: Callable[[float], None] = _real_sleep) -> None:
        self._interval = 1.0 / per_second
        self._clock = clock
        self._sleep = sleep
        self._next_at: float | None = None

    def wait(self) -> None:
        now = self._clock()
        if self._next_at is not None and now < self._next_at:
            self._sleep(self._next_at - now)
            now = self._next_at
        self._next_at = now + self._interval


_shared_limiter: RateLimiter | None = None


def _get_shared_limiter() -> RateLimiter:
    """One rate limiter per process, shared by every `_NCBIClient` that doesn't bring its own.

    NCBI's 3 req/s (10 with a key) applies across the whole run, not per call to
    `resolve()` — a loop that resolves many identifiers (the seeding script) must
    throttle itself against ITS OWN previous request, not just within one lookup.
    """
    global _shared_limiter
    if _shared_limiter is None:
        per_second = 10.0 if os.environ.get("NCBI_API_KEY") else 3.0
        _shared_limiter = RateLimiter(per_second)
    return _shared_limiter


def _default_fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "corpus-checker (+https://github.com/)"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


class _NCBIClient:
    """Rate-limited, retrying wrapper around NCBI eUtils.

    tool=corpus-checker is always sent. api_key= is added only when $NCBI_API_KEY is
    set (which also raises the limit to 10 req/s); email= only when $NCBI_EMAIL is
    set — no email address is ever hardcoded here.
    """

    def __init__(self, *, fetch: Callable[[str], bytes] | None = None,
                 limiter: RateLimiter | None = None,
                 sleep: Callable[[float], None] = _real_sleep) -> None:
        self._fetch = fetch or _default_fetch
        self._api_key = os.environ.get("NCBI_API_KEY")
        self._email = os.environ.get("NCBI_EMAIL")
        self._limiter = limiter or _get_shared_limiter()
        self._sleep = sleep

    def _url(self, endpoint: str, params: dict) -> str:
        params = dict(params)
        params["tool"] = "corpus-checker"
        if self._api_key:
            params["api_key"] = self._api_key
        if self._email:
            params["email"] = self._email
        return f"{EUTILS_BASE}{endpoint}?{urllib.parse.urlencode(params)}"

    def _get(self, endpoint: str, params: dict) -> bytes:
        url = self._url(endpoint, params)
        attempts = 4
        for attempt in range(attempts):
            self._limiter.wait()
            try:
                return self._fetch(url)
            except urllib.error.HTTPError as exc:
                retryable = exc.code == 429 or 500 <= exc.code < 600
                if not retryable or attempt == attempts - 1:
                    raise CrosswalkError(f"NCBI request failed ({exc.code}): {url}") from exc
                self._sleep(0.5 * (2 ** attempt))
            except urllib.error.URLError as exc:
                raise CrosswalkError(f"NCBI request failed: {url}: {exc}") from exc
        raise CrosswalkError(f"NCBI request failed after {attempts} attempts: {url}")  # pragma: no cover

    def esearch(self, *, db: str, term: str) -> dict:
        return json.loads(self._get("esearch.fcgi", {"db": db, "term": term, "retmode": "json"}))

    def esummary(self, *, db: str, ids: list[str]) -> dict:
        return json.loads(self._get("esummary.fcgi", {"db": db, "id": ",".join(ids), "retmode": "json"}))

    def elink(self, *, dbfrom: str, db: str, id: str) -> dict:
        return json.loads(self._get("elink.fcgi", {"dbfrom": dbfrom, "db": db, "id": id, "retmode": "json"}))

    def ecitmatch(self, *, bdata: str) -> str:
        raw = self._get("ecitmatch.cgi", {"db": "pubmed", "retmode": "xml", "bdata": bdata})
        return raw.decode("utf-8")


# --------------------------------------------------------------------------- GEO / PubMed parsing

def _parse_gds_entry(entry: dict) -> dict:
    sra = next((r["targetobject"] for r in entry.get("extrelations", []) if r.get("relationtype") == "SRA"), None)
    samples = sorted(
        ((s["accession"], s["title"]) for s in entry.get("samples", []) if s.get("accession")),
        key=lambda pair: pair[0],
    )
    return {
        "entrytype": entry.get("entrytype"),
        "accession": entry.get("accession"),
        "title": entry.get("title") or None,
        "organism": entry.get("taxon") or None,
        "pmids": list(entry.get("pubmedids") or []),
        "samples": samples,
        "bioproject": entry.get("bioproject") or None,
        "sra": sra,
    }


def _parse_pubmed_entry(entry: dict) -> dict:
    ids = {a["idtype"]: a["value"] for a in entry.get("articleids", []) if a.get("idtype")}
    return {"title": entry.get("title") or None, "doi": ids.get("doi"), "pmcid": ids.get("pmc")}


def _elink_targets(link: dict) -> list[str]:
    linksets = link.get("linksets") or []
    if not linksets:
        return []
    dbs = linksets[0].get("linksetdbs") or []
    return list(dbs[0].get("links", [])) if dbs else []


def _dedup(items: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item for item in items if item))


# --------------------------------------------------------------------------- resolvers, one per kind

def _resolve_geo(client: _NCBIClient, accession: str) -> CrosswalkRecord:
    prefix = accession[:3]   # "GSE" or "GSM" — esummary's entrytype uses the same three letters
    search = client.esearch(db="gds", term=f"{accession}[ACCN]")
    idlist = search.get("esearchresult", {}).get("idlist", [])
    if not idlist:
        raise CrosswalkError(f"no GEO record found for {accession!r} (esearch db=gds returned no uids)")

    summary = client.esummary(db="gds", ids=idlist)
    entries = [_parse_gds_entry(e) for uid, e in summary.get("result", {}).items() if uid != "uids"]
    match = next((e for e in entries if e["entrytype"] == prefix), None)
    if match is None:
        raise CrosswalkError(f"esearch found {accession!r} but no esummary result has entrytype={prefix!r}")

    pmids = match["pmids"]
    samples = tuple(match["samples"])
    sources = ["esearch.fcgi?db=gds", "esummary.fcgi?db=gds"]

    if not pmids and prefix == "GSM":
        # A sample record rarely carries its own PMID; its parent series does, and
        # the series is already in this same batch (esearch for a GSM accession
        # returns the parent GSE's uid alongside it — verified by hand, 2026-09-23).
        parent = next((e for e in entries if e["entrytype"] == "GSE"), None)
        if parent is not None:
            pmids = parent["pmids"]
            if not samples:
                samples = tuple(parent["samples"])

    accessions = [accession, match["bioproject"], match["sra"]]

    doi = pmcid = None
    if pmids:
        pubmed = client.esummary(db="pubmed", ids=pmids)
        sources.append("esummary.fcgi?db=pubmed")
        first = _parse_pubmed_entry(pubmed["result"][pmids[0]])
        doi, pmcid = first["doi"], first["pmcid"]

    return CrosswalkRecord(
        query=accession, accessions=_dedup(accessions), pmids=tuple(pmids),
        doi=doi, pmcid=pmcid, title=match["title"], organism=match["organism"], samples=samples,
        retrieved=_today(), sources=tuple(sources),
    )


def _resolve_pmid_core(client: _NCBIClient, pmid: str) -> CrosswalkRecord:
    pubmed = client.esummary(db="pubmed", ids=[pmid])
    sources = ["esummary.fcgi?db=pubmed"]
    article = _parse_pubmed_entry(pubmed["result"][pmid])

    link = client.elink(dbfrom="pubmed", db="gds", id=pmid)
    sources.append("elink.fcgi?dbfrom=pubmed&db=gds")
    gds_uids = _elink_targets(link)

    accessions: list[str] = []
    samples: tuple[tuple[str, str], ...] = ()
    organism = None
    if gds_uids:
        gds = client.esummary(db="gds", ids=gds_uids)
        sources.append("esummary.fcgi?db=gds")
        series = [_parse_gds_entry(e) for uid, e in gds.get("result", {}).items() if uid != "uids"]
        series = [s for s in series if s["entrytype"] == "GSE"]
        for s in series:
            accessions += [s["accession"], s["bioproject"], s["sra"]]
            samples += tuple(s["samples"])
        if series:
            organism = series[0]["organism"]
        samples = tuple(sorted(dict.fromkeys(samples), key=lambda pair: pair[0]))

    return CrosswalkRecord(
        query=pmid, accessions=_dedup(accessions), pmids=(pmid,),
        doi=article["doi"], pmcid=article["pmcid"], title=article["title"],
        organism=organism, samples=samples,
        retrieved=_today(), sources=tuple(sources),
    )


def _resolve_doi(client: _NCBIClient, doi: str) -> CrosswalkRecord:
    search = client.esearch(db="pubmed", term=f'"{doi}"[DOI]')
    idlist = search.get("esearchresult", {}).get("idlist", [])
    if not idlist:
        raise CrosswalkError(f"DOI {doi!r} did not resolve to a PubMed record (esearch db=pubmed found nothing)")
    record = _resolve_pmid_core(client, idlist[0])
    return replace(record, query=doi, sources=("esearch.fcgi?db=pubmed",) + record.sources)


def _biostudies_attr(section: dict, name: str) -> str | None:
    for a in section.get("attributes", []):
        if a.get("name") == name:
            return a.get("value")
    return None


def _resolve_arrayexpress(fetch: Callable[[str], bytes], accession: str) -> CrosswalkRecord:
    """EBI BioStudies. Optional: not part of the GSE/GSM/PMID/DOI chain.

    Not an NCBI endpoint, so the NCBI rate limiter and api_key/email params don't apply.
    """
    url = f"{BIOSTUDIES_BASE}{accession}"
    try:
        raw = fetch(url)
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        raise CrosswalkError(f"BioStudies request failed: {url}: {exc}") from exc
    doc = json.loads(raw)
    section = doc.get("section", {})
    title = _biostudies_attr(section, "Title")
    organism = _biostudies_attr(section, "Organism")

    pmid = doi = None
    for sub in section.get("subsections", []):
        if isinstance(sub, dict) and sub.get("type") == "Publication":
            accno = sub.get("accno", "")
            pmid = accno if accno.isdigit() else pmid
            doi = _biostudies_attr(sub, "DOI") or doi

    return CrosswalkRecord(
        query=accession, accessions=(accession,), pmids=(pmid,) if pmid else (),
        doi=doi, pmcid=None, title=title, organism=organism, samples=(),
        retrieved=_today(), sources=("biostudies:studies",),
    )


# --------------------------------------------------------------------------- public entry point

def resolve(identifier: str, *, network: bool = True, cache_dir: Path | None = None,
            fetch: Callable[[str], bytes] | None = None) -> CrosswalkRecord:
    """Resolve `identifier` (GSE…, GSM…, a PMID, or a DOI) to its canonical triple.

    Cache is read first, always. `network=False` never talks to NCBI: a cache miss
    raises `CacheMiss` rather than falling back. `fetch` replaces the urllib-based
    HTTP call; tests inject a fixture-replay function so this path never touches
    the network.
    """
    cache_dir = cache_dir or (repo_root() / "crosswalk" / "cache")
    kind, key = _classify(identifier)

    cached = _read_cache(cache_dir, kind, key)
    if cached is not None:
        return cached
    if not network:
        raise CacheMiss(f"no cached crosswalk entry for {identifier!r} (kind={kind}) and network=False")

    client = _NCBIClient(fetch=fetch)
    if kind == "geo":
        record = _resolve_geo(client, key)
    elif kind == "pubmed":
        record = _resolve_pmid_core(client, key)
    elif kind == "doi":
        record = _resolve_doi(client, key)
    elif kind == "arrayexpress":
        record = _resolve_arrayexpress(fetch or _default_fetch, key)
    else:  # pragma: no cover — _classify only returns the four kinds above
        raise CrosswalkError(f"unhandled identifier kind {kind!r}")

    _write_cache(cache_dir, kind, key, record)
    return record


# --------------------------------------------------------------------------- citation crosswalk

_CITATION_RE = re.compile(
    r"^(?P<author>[^,]+),\s*[A-Z](?:\.[A-Z])*\.?\s*(?:et al\.)?\s+"
    r"(?P<title>.+?)\.\s+"
    r"(?P<journal>[A-Z][A-Za-z0-9&.\- ]*?)\s+"
    r"(?P<volume>\d+),\s*"
    r"(?P<pages>[^(]+?)\s*"
    r"\((?P<year>\d{4})\)\.?\s*$"
)
_PAGE_RANGE_RE = re.compile(r"[–—-]")   # en dash, em dash, hyphen


def parse_citation(text: str) -> dict | None:
    """Extract (author, title, journal, volume, pages, first_page, year) from a Nature-style citation.

    Handles what Geneformer's manifest actually contains: en dashes
    in page ranges ('346–360'), '.e4'-style eLife/Cell Systems suffixes, bare
    article numbers with no page range at all ('eaba7721'), and non-ASCII surnames
    ('Litviňuková'). Returns None rather than guessing when the shape doesn't match —
    a citation that doesn't parse must not silently become a wrong match_citation() call.
    """
    m = _CITATION_RE.match(text.strip())
    if not m:
        return None
    pages = m.group("pages").strip()
    first_page = _PAGE_RANGE_RE.split(pages, maxsplit=1)[0].strip()
    return {
        "author": m.group("author").strip(),
        "title": m.group("title").strip(),
        "journal": m.group("journal").strip(),
        "volume": m.group("volume"),
        "pages": pages,
        "first_page": first_page,
        "year": m.group("year"),
    }


def _slug(s: str) -> str:
    ascii_s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_s.lower()).strip("-")
    return slug or "x"


def _citation_cache_key(journal: str, year: str, volume: str, first_page: str, author: str) -> str:
    return "_".join(_slug(str(p)) for p in (journal, year, volume, first_page, author))


def _parse_ecitmatch(text: str) -> str | None:
    """ECitMatch's reply is pipe-delimited, not XML, regardless of retmode=xml.

    A match ends the line with a bare PMID. A miss instead ends it with a code like
    'NOT_FOUND' or 'NOT_FOUND;INVALID_JOURNAL', dropping the author field along the
    way — so a plain 'last field is not all digits' check is the correct condition,
    not string length or field count.
    """
    last = text.strip().split("|")[-1]
    return last if last.isdigit() else None


def match_citation(journal: str, year: str, volume: str, first_page: str, author: str, *,
                    network: bool = True, cache_dir: Path | None = None,
                    fetch: Callable[[str], bytes] | None = None) -> str | None:
    """Resolve a citation to a PMID via NCBI ECitMatch, or None if it doesn't resolve.

    Never guesses: a non-match returns None and is cached as such, so a repeat
    lookup doesn't re-hit the network only to learn the same citation still fails.
    `network`/`cache_dir`/`fetch` follow `resolve()`'s pattern, because a cached,
    offline-testable ECitMatch call needs the same seam.
    """
    cache_dir = cache_dir or (repo_root() / "crosswalk" / "cache")
    key = _citation_cache_key(journal, year, volume, first_page, author)
    path = _cache_path(cache_dir, "citation", key)

    cached = _read_json(path)
    if cached is not None:
        return cached.get("pmid")
    if not network:
        raise CacheMiss(f"no cached ECitMatch entry for {(journal, year, volume, first_page, author)} "
                         "and network=False")

    client = _NCBIClient(fetch=fetch)
    bdata = f"{journal}|{year}|{volume}|{first_page}|{author}|key1|"
    pmid = _parse_ecitmatch(client.ecitmatch(bdata=bdata))

    _write_json(path, {
        "journal": journal, "year": year, "volume": volume, "first_page": first_page, "author": author,
        "pmid": pmid, "retrieved": _today(), "sources": ["ecitmatch.cgi?db=pubmed"],
    })
    return pmid
