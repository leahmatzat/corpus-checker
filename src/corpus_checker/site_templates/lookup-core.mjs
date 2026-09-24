/**
 * Dataset lookup: pure matching engine, no DOM, no network.
 *
 * A line-by-line port of the Python reference implementation — src/corpus_checker/lookup.py,
 * match.py, verdict.py, registry.py and normalize.py — kept close enough that
 * tests/fixtures/lookup_vectors.json (generated from the Python engine) can be replayed
 * against this module unchanged. If this file disagrees with a vector, THIS FILE is wrong.
 *
 * Field names on Answer/Query/Decision objects are snake_case on purpose, matching the
 * Python dataclasses and the vectors file exactly, so a vector's `expected` object can be
 * compared to this module's output field-by-field with no renaming in between.
 *
 * Network expansion (the live NCBI + BioStudies crosswalk) is NOT here — see lookup.js. This
 * module is imported by both lookup.js (the browser) and tests/js/lookup.test.mjs (Node, offline).
 */

// --------------------------------------------------------------------------- normalize.py port

const ACCESSION_RE = /(?<![A-Za-z0-9])(GS[EM]\d+|E-[A-Z]{4}-\d+|[SED]R[APRSX]\d+|PRJ[NED][A-Z]?\d+|EGA[SD]\d+|SCP\d+|HRA\d+|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?![0-9A-Za-z])/gi;
const DOI_RE = /10\.\d{4,9}\/[^\s|;,]+/gi;
const UUID_RE = /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;

function textOf(cell) {
  if (cell === null || cell === undefined) return "";
  const s = String(cell).trim();
  if (s.endsWith(".0") && /^\d+$/.test(s.slice(0, -2))) return s.slice(0, -2);
  return s;
}

/** Canonical case: UUIDs lower, repository accessions upper. Mirrors normalize.accession(). */
function accessionCanon(value) {
  value = String(value).trim();
  return UUID_RE.test(value) ? value.toLowerCase() : value.toUpperCase();
}

/** Every repository accession in a cell, canonical case, in order, without duplicates. */
function accessionsOf(cell) {
  const t = textOf(cell);
  const seen = new Map();
  for (const m of t.matchAll(ACCESSION_RE)) {
    const canon = accessionCanon(m[0]);
    if (!seen.has(canon)) seen.set(canon, true);
  }
  return [...seen.keys()];
}

function doisOf(cell) {
  const t = textOf(cell);
  const seen = new Map();
  for (const m of t.matchAll(DOI_RE)) {
    const d = m[0].replace(/\.+$/, "").toLowerCase();
    if (!seen.has(d)) seen.set(d, true);
  }
  return [...seen.keys()];
}

function doiCanon(value) {
  return String(value).trim().toLowerCase();
}

// --------------------------------------------------------------------------- lookup.py: parse / accession_type

const PMID_INPUT_RE = /^(?:pmid:?\s*)?(\d{1,9})$/i;
const PUBMED_URL_RE = /pubmed\.ncbi\.nlm\.nih\.gov\/(\d{1,9})/gi;

/** Identifier tokens in one line of user input. Mirrors lookup.parse(). */
export function parse(raw) {
  const text = String(raw).trim();
  const out = { accession: accessionsOf(text), doi: doisOf(text), pmid: [] };
  const m = PMID_INPUT_RE.exec(text);
  if (m) out.pmid.push(m[1]);
  for (const mm of text.matchAll(PUBMED_URL_RE)) out.pmid.push(mm[1]);
  return out;
}

// Deliberately case-sensitive (lowercase UUID only) — matches lookup.py's own local _UUID_RE,
// which (unlike normalize.py's) carries no re.IGNORECASE. Never invoked on a token that hasn't
// already been through normalize.accession(), so this asymmetry never bites in practice.
const ACCESSION_TYPE_UUID_RE = /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;
const ACCESSION_TYPE_PREFIXES = [
  ["GS", "geo"], ["E-", "arrayexpress"], ["PRJ", "bioproject"],
  ["EGA", "ega"], ["SCP", "scp"], ["HRA", "gsa"],
];

/** datasets/ identifier type for a canonical accession token. Mirrors lookup.accession_type(). */
export function accessionType(token) {
  const t = String(token).toUpperCase();
  if (ACCESSION_TYPE_UUID_RE.test(token)) return "cellxgene_dataset";
  for (const [prefix, kind] of ACCESSION_TYPE_PREFIXES) {
    if (t.startsWith(prefix)) return kind;
  }
  return "sra";
}

// --------------------------------------------------------------------------- verdict.py port

const PRESENT = "PRESENT", NOT_PRESENT = "NOT PRESENT", INCONCLUSIVE = "INCONCLUSIVE", NOT_CHECKABLE = "NOT CHECKABLE";
const EXACT_KEYS = new Set(["accession", "pmid", "doi"]);

function pyRepr(arr) {
  return "[" + arr.map((x) => `'${x}'`).join(", ") + "]";
}

/**
 * Mirrors verdict.decide_from(): same codes, same verdicts, reason text that reads like the
 * Python (not byte-identical — only verdict/code are part of the parity contract). Takes plain
 * values only, snake_case keys, so a vector's `input` object can be passed straight through.
 */
export function decideFrom({
  manifest_type, can_prove_presence, checkable = true,
  keys_attempted = [], keys_hit = [], listed_accessions = [], requires_keys = [],
  unconfirmed_sources = [], unsearched_sources = [],
} = {}) {
  const attempted = new Set(keys_attempted);
  const hit = new Set(keys_hit);
  const requires = new Set(requires_keys);
  const listed = [...listed_accessions].sort();
  const unconfirmed = [...unconfirmed_sources];
  const unsearched = [...unsearched_sources];

  if (manifest_type === "C-vague" || manifest_type === "D" || !checkable) {
    return { verdict: NOT_CHECKABLE, reason: "no manifest was published to search", code: "no_manifest" };
  }

  if (unconfirmed.length) {
    const state = hit.size ? "matched" : "did not match";
    return {
      verdict: INCONCLUSIVE,
      reason: `the manifest ${state}, but source(s) ${pyRepr(unconfirmed)} are not confirmed as the paper's `
        + "file (supplied, no url_hash / totals / author confirmation)",
      code: "unconfirmed_source",
    };
  }

  const hitExact = [...hit].filter((k) => EXACT_KEYS.has(k));
  if (hitExact.length) {
    const paperOnly = !hit.has("accession");
    if (!can_prove_presence) {
      const extra = paperOnly ? " The match is also on the paper, not the dataset." : "";
      return {
        verdict: INCONCLUSIVE,
        reason: "found in the pointed-to corpus, which the model trained on a subset of — a superset cannot "
          + `prove presence.${extra}`,
        code: "superset",
      };
    }
    if (paperOnly) {
      const where = listed.length ? `the manifest lists ${listed.join(", ")}` : "the manifest row names no accession";
      const keysStr = hitExact.slice().sort().map((k) => k.toUpperCase()).join("/");
      return {
        verdict: INCONCLUSIVE,
        reason: `same publication, matched on ${keysStr} only — ${where}; confirm it is the same dataset`,
        code: "same_publication",
      };
    }
    return { verdict: PRESENT, reason: null, code: "present" };
  }
  if (hit.size) {
    return {
      verdict: INCONCLUSIVE,
      reason: `matched on ${pyRepr([...hit].sort())} only — not an exact identifier`,
      code: "weak_only",
    };
  }

  if (unsearched.length) {
    return {
      verdict: INCONCLUSIVE,
      reason: `no match, but manifest source(s) ${pyRepr(unsearched)} were not searched`,
      code: "unsearched",
    };
  }
  const attemptedExact = [...attempted].filter((k) => EXACT_KEYS.has(k));
  if (!attemptedExact.length) {
    return {
      verdict: INCONCLUSIVE,
      reason: "no exact key could be attempted: the dataset has no confirmed identifier of a kind this "
        + "manifest carries",
      code: "no_exact_key",
    };
  }
  const missing = [...requires].filter((k) => !attempted.has(k)).sort();
  if (missing.length) {
    return {
      verdict: INCONCLUSIVE,
      reason: `no match on ${pyRepr([...attempted].sort())}, but this manifest needs ${pyRepr([...requires].sort())} `
        + `attempted before absence can be claimed; the dataset has no confirmed ${missing.join("/")} identifier`,
      code: "missing_required_key",
    };
  }
  return { verdict: NOT_PRESENT, reason: null, code: "not_present" };
}

// --------------------------------------------------------------------------- registry.py port

const IDENTIFIER_KEY = { pmid: "pmid", doi: "doi", url: null };

function identifierKey(type) {
  return Object.prototype.hasOwnProperty.call(IDENTIFIER_KEY, type) ? IDENTIFIER_KEY[type] : "accession";
}

/** Confirmed AND provisional identifiers count as usable; only `confirmed: false` never does. */
function usableIdentifiers(dataset, accessionTypes) {
  const usable = [];
  for (const ident of dataset.identifiers || []) {
    const key = identifierKey(ident.type || "");
    if (!(ident.confirmed === true || ident.confirmed === "provisional") || !key) continue;
    if (key === "accession" && accessionTypes != null && !accessionTypes.includes(ident.type)) continue;
    usable.push(ident);
  }
  return usable;
}

function confirmedKeys(dataset, accessionTypes) {
  return new Set(usableIdentifiers(dataset, accessionTypes).map((i) => identifierKey(i.type)));
}

/** Keys whose only usable identifiers are provisional, and the bases of those identifiers. */
function provisionalKeys(dataset, accessionTypes) {
  const byKey = new Map();
  for (const ident of usableIdentifiers(dataset, accessionTypes)) {
    const key = identifierKey(ident.type);
    if (!byKey.has(key)) byKey.set(key, []);
    byKey.get(key).push(ident);
  }
  const keys = new Set();
  for (const [key, idents] of byKey) {
    if (idents.every((i) => i.confirmed === "provisional")) keys.add(key);
  }
  const bases = new Map();
  for (const key of [...keys].sort()) {
    for (const i of byKey.get(key)) {
      if (i.basis && !bases.has(i.basis)) bases.set(i.basis, true);
    }
  }
  return { keys, bases: [...bases.keys()] };
}

/** ("provisional", basis) if any attempted key relies only on provisional identifiers, else (null, null). */
function identityOf(dataset, keysAttempted, accessionTypes) {
  const { keys, bases } = provisionalKeys(dataset, accessionTypes);
  const attempted = new Set(keysAttempted);
  const relevant = [...keys].some((k) => attempted.has(k));
  if (relevant) return { identity: "provisional", identity_basis: bases.length ? bases.join(" ") : null };
  return { identity: null, identity_basis: null };
}

// --------------------------------------------------------------------------- match.py port

function targets(dataset, accessionTypes) {
  const out = { accession: new Set(), pmid: new Set(), doi: new Set() };
  for (const ident of usableIdentifiers(dataset, accessionTypes)) {
    const key = identifierKey(ident.type);
    const value = String(ident.value).trim();
    if (key === "accession") out.accession.add(accessionCanon(value));
    else if (key === "doi") out.doi.add(doiCanon(value));
    else if (key === "pmid") out.pmid.add(value);
  }
  return out;
}

/** RecordIndex.lookup()/samples_in() + match.match(), combined: needs a `corpus` built by loadIndex(). */
function matchRecords(corpus, dataset, keys, accessionTypes) {
  const wanted = targets(dataset, accessionTypes);
  const hits = {};
  for (const key of keys) {
    const found = new Map();
    for (const value of [...(wanted[key] || [])].sort()) {
      for (const r of corpus.lookup(key, value)) found.set(r, r);
    }
    hits[key] = [...found.values()];
  }
  const evidence = new Map();
  for (const key of keys) {
    for (const r of hits[key]) {
      if (r.sample) {
        evidence.set(r, r);
      } else if (r.project) {
        for (const s of corpus.samplesIn(r.project)) evidence.set(s, s);
      }
    }
  }
  return { keys_attempted: keys, hits, evidence: [...evidence.values()] };
}

function keysHitOf(mr) {
  return mr.keys_attempted.filter((k) => mr.hits[k] && mr.hits[k].length > 0);
}

function matchLevelOf(hit) {
  if (hit.includes("accession")) return "dataset";
  if (hit.includes("pmid") || hit.includes("doi")) return "publication";
  return null;
}

function sampleIdsOf(mr) {
  const ids = [];
  for (const r of mr.evidence) {
    const tokens = accessionsOf(r.sample);
    ids.push(tokens.length ? tokens[0] : r.sample);
  }
  return [...new Set(ids)].sort();
}

function cellsOf(mr) {
  const counts = mr.evidence.map((r) => r.cells).filter((c) => c !== null && c !== undefined);
  return counts.length ? counts.reduce((a, b) => a + b, 0) : null;
}

function listedAccessionsOf(mr) {
  const seen = new Set();
  for (const key of Object.keys(mr.hits)) {
    for (const r of mr.hits[key]) {
      if (r.accessions && r.accessions.length) seen.add(r.accessions[0]);
    }
  }
  return [...seen].sort();
}

// --------------------------------------------------------------------------- lookup.py: load_corpora / build_lookup_index mirror

function addToMap(map, key, record) {
  if (!map.has(key)) map.set(key, []);
  map.get(key).push(record);
}

function buildCorpus(c) {
  const byAccession = new Map();
  const byPmid = new Map();
  const byDoi = new Map();
  const samplesByProject = new Map();
  const records = [];
  for (const row of c.rows || []) {
    const [sourceIndex, project, sample, cells, accessions, pmids, dois] = row;
    const source = sourceIndex >= 0 ? c.sources[sourceIndex] : null;
    const record = { source, project, sample, cells, accessions, pmids, dois };
    records.push(record);
    for (const a of accessions) addToMap(byAccession, a, record);
    for (const p of pmids) addToMap(byPmid, p, record);
    for (const dv of dois) addToMap(byDoi, dv, record);
    if (sample && project) {
      if (!samplesByProject.has(project)) samplesByProject.set(project, []);
      samplesByProject.get(project).push(record);
    }
  }
  return {
    model: c.model,
    model_name: c.model_name,
    stage: c.stage,
    manifest_type: c.manifest_type,
    checkable: c.checkable,
    indexed: c.indexed,
    unavailable: c.unavailable,
    can_prove_presence: c.can_prove_presence,
    keys_available: new Set(c.keys_available || []),
    requires_keys: c.requires_keys || [],
    accession_types: c.accession_types || null,
    sources: c.sources || [],
    sources_searched: c.sources_searched || [],
    unconfirmed_sources: c.unconfirmed_sources || [],
    verified_by: c.verified_by,
    verified_date: c.verified_date,
    draft: c.draft,
    records,
    lookup(key, value) {
      const m = key === "accession" ? byAccession : key === "pmid" ? byPmid : byDoi;
      return m.get(value) || [];
    },
    samplesIn(project) {
      return samplesByProject.get(project) || [];
    },
  };
}

/**
 * Rebuilds the token maps and sample-rows-by-project from build_lookup_index()'s compact rows.
 * `indexJson` is the parsed contents of lookup-index.json.
 */
export function loadIndex(indexJson) {
  const corpora = (indexJson.corpora || []).map(buildCorpus);
  const catalog = indexJson.catalog || {};
  const recorded = indexJson.recorded || [];
  return { corpora, catalog, recorded, disclaimer: indexJson.disclaimer, index_version: indexJson.index_version };
}

// --------------------------------------------------------------------------- lookup.py: _catalog_match

/**
 * A datasets/ entry, by id, by name, or by a shared accession — confirmed or provisional, never
 * by paper. `catalog[id].identifiers` entries are `[type, value]` or `[type, value, "provisional",
 * basis]`; only `value` (index 1) matters for this lookup, so both shapes work unchanged.
 */
export function catalogMatch(raw, accessions, catalog) {
  const key = String(raw).trim().toLowerCase();
  const entries = Object.entries(catalog || {});
  for (const [dId, d] of entries) {
    const name = String(d.name || "").toLowerCase();
    if (key === dId.toLowerCase() || key === name) return dId;
  }
  const accSet = accessions instanceof Set ? accessions : new Set(accessions || []);
  const sorted = entries.slice().sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
  for (const [dId, d] of sorted) {
    for (const ident of d.identifiers || []) {
      const value = ident[1];
      if (accSet.has(accessionCanon(String(value)))) return dId;
    }
  }
  return null;
}

// --------------------------------------------------------------------------- lookup.py: _answer / _recorded / search

function answerFor(corpus, query) {
  const base = {
    model: corpus.model, model_name: corpus.model_name, stage: corpus.stage,
    sources_searched: corpus.sources_searched, verified_by: corpus.verified_by,
    verified_date: corpus.verified_date, draft: corpus.draft, recorded: false,
  };
  const unknownIdentity = { identity: null, identity_basis: null };
  if (!corpus.checkable) {
    const d = decideFrom({ manifest_type: corpus.manifest_type, can_prove_presence: false, checkable: false });
    return {
      ...base, ...unknownIdentity, verdict: d.verdict, code: d.code, reason: d.reason, match_level: null,
      keys_attempted: [], matched_on: [], samples: null, cells: null, sample_ids: [], listed_accessions: [],
    };
  }
  if (!corpus.indexed) {
    return {
      ...base, ...unknownIdentity, verdict: "INCONCLUSIVE", code: "not_indexed",
      reason: `this manifest is not available to search here (${corpus.unavailable})`,
      match_level: null, keys_attempted: [], matched_on: [], samples: null, cells: null,
      sample_ids: [], listed_accessions: [],
    };
  }

  const provisionalMap = new Map((query.provisional || []).map(([t, v, basis]) => [`${t}\u0000${v}`, basis]));
  const dataset = {
    id: "query",
    identifiers: query.identifiers.map(([t, v]) => {
      const pk = `${t}\u0000${v}`;
      return provisionalMap.has(pk)
        ? { type: t, value: v, confirmed: "provisional", basis: provisionalMap.get(pk) }
        : { type: t, value: v, confirmed: true };
    }),
  };
  const accessionTypes = corpus.accession_types;
  if (accessionTypes && accessionTypes.includes("cellxgene_collection")) {
    // A UUID typed by the user may be a collection or a dataset id; offer it as both, keeping
    // whatever confirmed/basis the original identifier carried (Python: {**i, "type": ...}).
    const extra = dataset.identifiers
      .filter((i) => i.type === "cellxgene_dataset")
      .map((i) => ({ ...i, type: "cellxgene_collection" }));
    dataset.identifiers = dataset.identifiers.concat(extra);
  }
  const keys = [...confirmedKeys(dataset, accessionTypes)]
    .filter((k) => corpus.keys_available.has(k) && EXACT_KEYS.has(k))
    .sort();

  const mr = matchRecords(corpus, dataset, keys, accessionTypes);
  const hit = keysHitOf(mr);
  const matchLevel = matchLevelOf(hit);
  const sampleIds = sampleIdsOf(mr);
  const cells = cellsOf(mr);
  const listedAccessions = listedAccessionsOf(mr);
  const d = decideFrom({
    manifest_type: corpus.manifest_type, can_prove_presence: corpus.can_prove_presence,
    keys_attempted: keys, keys_hit: hit, listed_accessions: listedAccessions,
    requires_keys: corpus.requires_keys, unconfirmed_sources: corpus.unconfirmed_sources,
  });
  const { identity, identity_basis } = identityOf(dataset, keys, accessionTypes);
  return {
    ...base, verdict: d.verdict, code: d.code, reason: d.reason, match_level: matchLevel,
    identity, identity_basis,
    keys_attempted: keys, matched_on: hit, samples: sampleIds.length || null, cells,
    sample_ids: sampleIds, listed_accessions: listedAccessions,
  };
}

function recordedAnswer(corpus, finding) {
  return {
    model: corpus.model, model_name: corpus.model_name, stage: corpus.stage,
    verdict: finding.verdict, code: "recorded", reason: finding.reason ?? null,
    match_level: finding.match_level ?? null,
    keys_attempted: finding.keys_attempted ?? [], matched_on: finding.matched_on ?? [],
    samples: finding.samples ?? null, cells: finding.cells ?? null,
    sample_ids: finding.sample_ids ?? [], listed_accessions: [],
    sources_searched: corpus.sources_searched, recorded: true,
    identity: finding.identity ?? null, identity_basis: finding.identity_basis ?? null,
    verified_by: corpus.verified_by, verified_date: corpus.verified_date, draft: corpus.draft,
  };
}

/**
 * Answer one query against every loaded corpus. Mirrors lookup.search(): recorded findings win
 * for catalog datasets, `not_indexed` for corpora with indexed:false, accession_types filtering,
 * the cellxgene UUID-as-collection duplication in _answer(). `loaded` is loadIndex()'s return.
 */
export function search(query, loaded) {
  const { corpora, recorded } = loaded;
  const answers = [];
  if (query.kind !== "unrecognised") {
    for (const c of corpora) {
      const rec = query.catalog_id
        ? recorded.find((r) => r.dataset === query.catalog_id && r.model === c.model && r.stage === c.stage)
        : undefined;
      answers.push(rec ? recordedAnswer(c, rec) : answerFor(c, query));
    }
  }
  const paperLevelWarning = query.kind === "paper" || answers.some((a) => a.match_level === "publication");
  const seen = new Set();
  const searched = [];
  for (const c of corpora) {
    if (!seen.has(c.model)) { seen.add(c.model); searched.push(c.model); }
  }
  return { answers, searched, paperLevelWarning };
}

// --------------------------------------------------------------------------- lookup.py: plain-language answers

const SHORT = { PRESENT: "Yes", "NOT PRESENT": "No", INCONCLUSIVE: "Can't tell", "NOT CHECKABLE": "Unknown" };

export function short(verdict) {
  return SHORT[verdict] ?? verdict;
}

function fmtInt(n) {
  return n.toLocaleString("en-US");
}

/** One plain sentence per answer. lead=false drops the leading Yes/No/… for tables that show it. */
export function answerText(a, { lead = true } = {}) {
  const where = `${a.model_name}'s ${a.stage} data`;
  const name = (k) => (k === "pmid" || k === "doi" ? k.toUpperCase() : k);
  let body;
  if (a.verdict === "PRESENT") {
    const parts = [];
    if (a.samples) parts.push(`${a.samples} samples`);
    if (a.cells !== null && a.cells !== undefined) parts.push(`${fmtInt(a.cells)} cells`);
    const size = parts.join(", ");
    const keys = a.matched_on.map(name).join(" and ");
    body = `in ${where}` + (size ? `: ${size}` : "") + (keys ? ` (matched on ${keys})` : "") + ".";
  } else if (a.verdict === "NOT PRESENT") {
    body = `not in ${where}; searched ${a.sources_searched.join(", ")} on ${a.keys_attempted.map(name).join(" and ")}.`;
  } else if (a.verdict === "NOT CHECKABLE") {
    body = `${a.model_name} did not publish a training-data list for its ${a.stage} stage.`;
  } else {
    body = a.reason ? `${a.reason.replace(/\.+$/, "")}.` : "";
  }
  if (a.identity === "provisional") {
    const basisText = a.identity_basis ? a.identity_basis.replace(/\.+$/, "") : "basis not recorded";
    body += ` (provisional identity: ${basisText}.)`;
  }
  return lead ? `${short(a.verdict)} — ${body}` : body;
}

export function identifierLabel(kind, value) {
  if (kind === "pmid") return `PMID ${value}`;
  if (kind === "doi") return `DOI ${value}`;
  return value;
}

export function provenanceText(a) {
  if (a.draft) return "draft entry — not yet verified";
  return `manifest recorded by ${a.verified_by}, ${a.verified_date}`;
}
