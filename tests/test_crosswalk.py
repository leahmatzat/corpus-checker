"""crosswalk.py, fully offline: every NCBI response is a fixture recorded once under
tests/fixtures/ncbi/ (see the file for provenance) and replayed through the
injectable `fetch`. Nothing here opens a socket except the single @pytest.mark.network
test, which default addopts (-m "not network", pyproject.toml) excludes.
"""
from __future__ import annotations

import json
import urllib.error
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from corpus_checker import crosswalk

FIXTURES = Path(__file__).parent / "fixtures" / "ncbi"


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch):
    """No real sleeping, and no host NCBI_API_KEY/NCBI_EMAIL leaking into a test's expectations."""
    monkeypatch.setattr(crosswalk.time, "sleep", lambda seconds: None)
    monkeypatch.delenv("NCBI_API_KEY", raising=False)
    monkeypatch.delenv("NCBI_EMAIL", raising=False)


def _replay(rules):
    """rules: [(predicate(endpoint, params) -> bool, fixture_filename), ...], first match wins.

    Routes on the DECODED query params rather than the raw URL string, so the test
    doesn't depend on urlencode's exact escaping of '[', ']', '"', ','.
    """
    def fetch(url: str) -> bytes:
        parsed = urlparse(url)
        endpoint = parsed.path.rsplit("/", 1)[-1]
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        for predicate, filename in rules:
            if predicate(endpoint, params):
                return (FIXTURES / filename).read_bytes()
        raise AssertionError(f"no fixture rule matched {endpoint} {params}")
    return fetch


def _is_gds_summary_for(uid_prefix: str):
    return lambda e, p: e == "esummary.fcgi" and p.get("db") == "gds" and p.get("id", "").startswith(uid_prefix)


# The Norman 2019 chain, as verified by hand against NCBI:
#   GSE133344 -> esearch db=gds -> uid 200133344 -> esummary -> pubmedids ["31395745"]
#   -> esummary db=pubmed id=31395745 -> doi 10.1126/science.aax4438, pmcid PMC6746554
GSE_CHAIN_RULES = [
    (lambda e, p: e == "esearch.fcgi" and p.get("db") == "gds" and p.get("term") == "GSE133344[ACCN]",
     "esearch_gds_gse133344.json"),
    (_is_gds_summary_for("200133344,"), "esummary_gds_gse133344_batch.json"),
    (lambda e, p: e == "esummary.fcgi" and p.get("db") == "pubmed" and p.get("id") == "31395745",
     "esummary_pubmed_31395745.json"),
]

PMID_CHAIN_RULES = [
    (lambda e, p: e == "esummary.fcgi" and p.get("db") == "pubmed" and p.get("id") == "31395745",
     "esummary_pubmed_31395745.json"),
    (lambda e, p: e == "elink.fcgi" and p.get("dbfrom") == "pubmed" and p.get("db") == "gds"
     and p.get("id") == "31395745", "elink_pubmed_gds_31395745.json"),
    (_is_gds_summary_for("200133344"), "esummary_gds_200133344.json"),
]

DOI_CHAIN_RULES = [
    (lambda e, p: e == "esearch.fcgi" and p.get("db") == "pubmed" and p.get("term") == '"10.1126/science.aax4438"[DOI]',
     "esearch_pubmed_doi_norman.json"),
    *PMID_CHAIN_RULES,
]


# --------------------------------------------------------------------------- resolve(): the GSE chain

def test_resolve_gse_returns_norman_2019_triple(tmp_path):
    record = crosswalk.resolve("GSE133344", cache_dir=tmp_path, fetch=_replay(GSE_CHAIN_RULES))

    assert record.query == "GSE133344"
    assert record.pmids == ("31395745",)
    assert record.doi == "10.1126/science.aax4438"
    assert record.pmcid == "PMC6746554"
    assert "genetic interaction manifolds" in record.title
    assert record.organism == "Homo sapiens"
    assert "GSE133344" in record.accessions
    assert "PRJNA551220" in record.accessions          # bioproject, from the esummary extrelations/bioproject fields
    assert "SRP212114" in record.accessions            # SRA study, from extrelations
    assert len(record.samples) == 16
    assert ("GSM3906020", "sgRNA perturb-seq experiment (gemgroup 1)") in record.samples
    assert record.retrieved == crosswalk._today()
    assert record.sources == ("esearch.fcgi?db=gds", "esummary.fcgi?db=gds", "esummary.fcgi?db=pubmed")

    # and it was cached under crosswalk/cache/geo/GSE133344.json, pretty-printed with sorted keys
    cached_path = tmp_path / "geo" / "GSE133344.json"
    assert cached_path.is_file()
    raw = cached_path.read_text(encoding="utf-8")
    assert json.loads(raw) == crosswalk.as_dict(record)
    assert list(json.loads(raw).keys()) == sorted(json.loads(raw).keys())


def test_resolve_gse_is_idempotent_from_cache_without_refetching(tmp_path):
    fetch = _replay(GSE_CHAIN_RULES)
    first = crosswalk.resolve("GSE133344", cache_dir=tmp_path, fetch=fetch)

    def _boom(url):
        raise AssertionError(f"should not fetch on a cache hit: {url}")

    second = crosswalk.resolve("GSE133344", cache_dir=tmp_path, fetch=_boom)
    assert second == first


# --------------------------------------------------------------------------- resolve(): PMID input

def test_resolve_pmid_input_reaches_same_geo_series(tmp_path):
    record = crosswalk.resolve("31395745", cache_dir=tmp_path, fetch=_replay(PMID_CHAIN_RULES))
    assert record.query == "31395745"
    assert record.pmids == ("31395745",)
    assert record.doi == "10.1126/science.aax4438"
    assert record.pmcid == "PMC6746554"
    assert record.organism == "Homo sapiens"
    assert "GSE133344" in record.accessions
    assert len(record.samples) == 16
    assert record.sources == ("esummary.fcgi?db=pubmed", "elink.fcgi?dbfrom=pubmed&db=gds", "esummary.fcgi?db=gds")


# --------------------------------------------------------------------------- resolve(): DOI input

def test_resolve_doi_input_resolves_through_pmid(tmp_path):
    record = crosswalk.resolve("10.1126/science.aax4438", cache_dir=tmp_path,
                                fetch=_replay(DOI_CHAIN_RULES))
    assert record.query == "10.1126/science.aax4438"
    assert record.pmids == ("31395745",)
    assert "GSE133344" in record.accessions
    assert record.sources[0] == "esearch.fcgi?db=pubmed"
    assert record.sources[1:] == ("esummary.fcgi?db=pubmed", "elink.fcgi?dbfrom=pubmed&db=gds", "esummary.fcgi?db=gds")


def test_doi_input_is_case_and_cache_normalised(tmp_path):
    # DOIs normalise to lowercase for both the record's query and the cache filename.
    crosswalk.resolve("10.1126/SCIENCE.AAX4438", cache_dir=tmp_path, fetch=_replay(DOI_CHAIN_RULES))
    assert (tmp_path / "doi" / "10.1126_science.aax4438.json").is_file()


# --------------------------------------------------------------------------- cache-first / --no-network

def test_cache_hit_needs_no_fetch_function_at_all(tmp_path):
    crosswalk.resolve("GSE133344", cache_dir=tmp_path, fetch=_replay(GSE_CHAIN_RULES))
    # network=False and no fetch supplied: must resolve purely from the cache written above.
    record = crosswalk.resolve("GSE133344", network=False, cache_dir=tmp_path)
    assert record.doi == "10.1126/science.aax4438"


def test_cache_miss_with_no_network_raises_cache_miss(tmp_path):
    with pytest.raises(crosswalk.CacheMiss):
        crosswalk.resolve("GSE133344", network=False, cache_dir=tmp_path)


def test_unrecognised_identifier_raises_before_touching_cache_or_network(tmp_path):
    with pytest.raises(crosswalk.CrosswalkError):
        crosswalk.resolve("not-an-identifier", cache_dir=tmp_path, fetch=lambda url: (_ for _ in ()).throw(
            AssertionError("must not fetch")))


# --------------------------------------------------------------------------- DOI cache-key reversibility

@pytest.mark.parametrize("doi", [
    "10.1126/science.aax4438",
    "10.1016/j.cell.2016.11.048",
    "10.1000/some_suffix/with_underscores",   # the case the '_' escaping exists for
])
def test_doi_cache_key_round_trips(doi):
    encoded = crosswalk._encode_doi(doi)
    assert "/" not in encoded
    assert crosswalk._decode_doi(encoded) == doi.lower()


# --------------------------------------------------------------------------- rate limiter

def test_rate_limiter_spaces_calls_without_real_sleeping():
    clock_value = [0.0]
    sleeps: list[float] = []

    def clock():
        return clock_value[0]

    def sleep(seconds):
        sleeps.append(seconds)
        clock_value[0] += seconds   # simulate time passing while "asleep"

    limiter = crosswalk.RateLimiter(2.0, clock=clock, sleep=sleep)   # 0.5s spacing

    limiter.wait()                  # first call: nothing to wait for
    assert sleeps == []

    clock_value[0] += 0.1           # only 0.1s elapsed before the next request
    limiter.wait()
    assert sleeps == pytest.approx([0.4])   # needed 0.5s spacing, had 0.1s -> sleep 0.4s

    clock_value[0] += 1.0           # plenty of time has passed
    limiter.wait()
    assert sleeps == pytest.approx([0.4])   # unchanged: no sleep needed this time


def test_shared_ncbi_client_defaults_to_3_per_second_unkeyed(monkeypatch):
    monkeypatch.delenv("NCBI_API_KEY", raising=False)
    monkeypatch.setattr(crosswalk, "_shared_limiter", None)
    limiter = crosswalk._get_shared_limiter()
    assert limiter._interval == pytest.approx(1 / 3)


def test_shared_ncbi_client_uses_10_per_second_with_api_key(monkeypatch):
    monkeypatch.setenv("NCBI_API_KEY", "dummy-key")
    monkeypatch.setattr(crosswalk, "_shared_limiter", None)
    limiter = crosswalk._get_shared_limiter()
    assert limiter._interval == pytest.approx(1 / 10)


# --------------------------------------------------------------------------- retry / backoff on 429 / 5xx

def test_retries_on_503_then_succeeds():
    calls = {"n": 0}

    def flaky_fetch(url: str) -> bytes:
        calls["n"] += 1
        if calls["n"] < 3:
            raise urllib.error.HTTPError(url, 503, "Service Unavailable", None, None)
        return b'{"ok": true}'

    client = crosswalk._NCBIClient(
        fetch=flaky_fetch,
        limiter=crosswalk.RateLimiter(1000.0, clock=lambda: 0.0, sleep=lambda s: None),
        sleep=lambda s: None,
    )
    result = client._get("esearch.fcgi", {"db": "gds", "term": "x"})
    assert result == b'{"ok": true}'
    assert calls["n"] == 3


def test_non_retryable_http_error_raises_immediately():
    def bad_fetch(url: str) -> bytes:
        raise urllib.error.HTTPError(url, 404, "Not Found", None, None)

    client = crosswalk._NCBIClient(
        fetch=bad_fetch,
        limiter=crosswalk.RateLimiter(1000.0, clock=lambda: 0.0, sleep=lambda s: None),
        sleep=lambda s: None,
    )
    with pytest.raises(crosswalk.CrosswalkError):
        client._get("esearch.fcgi", {"db": "gds", "term": "x"})


# --------------------------------------------------------------------------- parse_citation

BARON_CITATION = (
    "Baron, M. et al. A Single-Cell Transcriptomic Map of the Human and Mouse Pancreas Reveals "
    "Inter- and Intra-cell Population Structure. Cell Syst 3, 346–360.e4 (2016)."
)
LITVINUKOVA_CITATION = "Litviňuková, M. et al. Cells of the adult human heart. Nature 588, 466–472 (2020)."
DOMINGUEZ_CONDE_CITATION = (
    "Domínguez Conde, C. et al. Cross-tissue immune cell analysis reveals tissue-specific features "
    "in humans. Science 376, eabl5197 (2022)."
)
STUART_CITATION = "Stuart, T. et al. Comprehensive Integration of Single-Cell Data. Cell 177, 1888–1902.e21 (2019)."


def test_parse_citation_baron_en_dash_and_e_suffix():
    parsed = crosswalk.parse_citation(BARON_CITATION)
    assert parsed == {
        "author": "Baron", "journal": "Cell Syst", "volume": "3",
        "pages": "346–360.e4", "first_page": "346", "year": "2016",
        "title": "A Single-Cell Transcriptomic Map of the Human and Mouse Pancreas Reveals "
                 "Inter- and Intra-cell Population Structure",
    }


def test_parse_citation_litvinukova_non_ascii_surname():
    parsed = crosswalk.parse_citation(LITVINUKOVA_CITATION)
    assert parsed["author"] == "Litviňuková"
    assert parsed["journal"] == "Nature"
    assert parsed["volume"] == "588"
    assert parsed["first_page"] == "466"
    assert parsed["year"] == "2020"


def test_parse_citation_dominguez_conde_article_number_no_page_range():
    parsed = crosswalk.parse_citation(DOMINGUEZ_CONDE_CITATION)
    assert parsed["author"] == "Domínguez Conde"
    assert parsed["pages"] == "eabl5197"
    assert parsed["first_page"] == "eabl5197"   # no dash: the article number IS the whole "page"


def test_parse_citation_stuart_e_suffix_range():
    parsed = crosswalk.parse_citation(STUART_CITATION)
    assert parsed["journal"] == "Cell"
    assert parsed["volume"] == "177"
    assert parsed["first_page"] == "1888"
    assert parsed["year"] == "2019"


def test_parse_citation_returns_none_for_unparseable_text():
    assert crosswalk.parse_citation("not a citation at all") is None
    assert crosswalk.parse_citation("") is None


# --------------------------------------------------------------------------- match_citation / ECitMatch replay

def _ecitmatch_replay(fixture: str, expected_bdata_prefix: str):
    def fetch(url: str) -> bytes:
        params = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        assert params.get("bdata", "").startswith(expected_bdata_prefix), params.get("bdata")
        return (FIXTURES / fixture).read_bytes()
    return fetch


def test_match_citation_baron_resolves(tmp_path):
    pmid = crosswalk.match_citation("Cell Syst", "2016", "3", "346", "Baron", cache_dir=tmp_path,
                                     fetch=_ecitmatch_replay("ecitmatch_baron.txt", "Cell Syst|2016|3|346|Baron"))
    assert pmid == "27667365"


def test_match_citation_litvinukova_resolves_with_non_ascii_author(tmp_path):
    pmid = crosswalk.match_citation("Nature", "2020", "588", "466", "Litviňuková", cache_dir=tmp_path,
                                     fetch=_ecitmatch_replay("ecitmatch_litvinukova.txt", "Nature|2020|588|466"))
    assert pmid == "32971526"


def test_match_citation_dominguez_conde_resolves(tmp_path):
    pmid = crosswalk.match_citation("Science", "2022", "376", "eabl5197", "Domínguez Conde", cache_dir=tmp_path,
                                     fetch=_ecitmatch_replay("ecitmatch_dominguez_conde.txt", "Science|2022|376|eabl5197"))
    assert pmid == "35549406"


def test_match_citation_stuart_resolves(tmp_path):
    pmid = crosswalk.match_citation("Cell", "2019", "177", "1888", "Stuart", cache_dir=tmp_path,
                                     fetch=_ecitmatch_replay("ecitmatch_stuart.txt", "Cell|2019|177|1888|Stuart"))
    assert pmid == "31178118"


def test_match_citation_no_match_returns_none_and_caches_the_miss(tmp_path):
    fetch = _ecitmatch_replay("ecitmatch_nomatch.txt", "Nonexistent J|2099|999|1|Nobody")
    pmid = crosswalk.match_citation("Nonexistent J", "2099", "999", "1", "Nobody", cache_dir=tmp_path, fetch=fetch)
    assert pmid is None

    # the miss itself is cached, so a repeat lookup never touches the network again
    def _boom(url):
        raise AssertionError("should not fetch: the miss was already cached")
    again = crosswalk.match_citation("Nonexistent J", "2099", "999", "1", "Nobody", cache_dir=tmp_path, fetch=_boom)
    assert again is None


def test_match_citation_no_network_cache_miss_raises(tmp_path):
    with pytest.raises(crosswalk.CacheMiss):
        crosswalk.match_citation("Cell Syst", "2016", "3", "346", "Baron", network=False, cache_dir=tmp_path)


# --------------------------------------------------------------------------- one live test, excluded by default

@pytest.mark.network
def test_live_resolve_gse133344(tmp_path):
    record = crosswalk.resolve("GSE133344", cache_dir=tmp_path)
    assert record.doi == "10.1126/science.aax4438"
