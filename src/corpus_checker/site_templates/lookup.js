/**
 * Dataset lookup page: expansion through live NCBI + EBI BioStudies calls, then wires the
 * results from lookup-core.mjs into the DOM. Never imported by tests/js/ — the network layer
 * lives only here, separate from lookup-core.mjs, so the parity tests never touch the network.
 *
 * Mirrors src/corpus_checker/lookup.py's interpret(): a GEO series (GSE) widens to its paper's
 * PMID/DOI and to the series' BioProject/SRA ids; a single sample (GSM) never widens to its
 * series; an ArrayExpress accession resolves through EBI BioStudies for its publication's
 * PMID/DOI; a PMID or DOI is answered at the paper level, then once per GSE it links to.
 * CELLxGENE UUIDs and SRA/BioProject/EGA/SCP/HRA ids are never sent anywhere — they are
 * searched exactly as typed.
 */
import {
  parse, accessionType, loadIndex, catalogMatch, search, answerText, provenanceText,
  identifierLabel, short,
} from "./lookup-core.mjs";

const EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/";
const BIOSTUDIES_BASE = "https://www.ebi.ac.uk/biostudies/api/v1/studies/";
const TIMEOUT_MS = 10000;
const RATE_PER_SECOND = 3;

const GEO_RE = /^GS[EM]\d+$/i;
const PMID_RE = /^\d{1,9}$/;
const DOI_RE = /^10\.\d{4,}\/\S+$/i;
const ARRAYEXPRESS_RE = /^E-[A-Z]{4}-\d+$/i;
const NCBI_RESOLVABLE = /^(GS[EM]\d+|E-[A-Z]{4}-\d+|\d{1,9}|10\.\d{4,}\/\S+)$/i;

// --------------------------------------------------------------------------- rate limiting + fetch

function sleep(ms) {
  return new Promise((resolve) => { setTimeout(resolve, ms); });
}

export class RateLimiter {
  constructor(perSecond) {
    this.interval = 1000 / perSecond;
    this.nextAt = null;
  }

  async wait() {
    const now = Date.now();
    if (this.nextAt !== null && now < this.nextAt) {
      await sleep(this.nextAt - now);
      this.nextAt += this.interval;
    } else {
      this.nextAt = now + this.interval;
    }
  }
}

// Shared across NCBI and BioStudies: both are being polite to a free public API, and one
// dataset lookup makes only a handful of calls either way.
const limiter = new RateLimiter(RATE_PER_SECOND);

async function fetchJson(url) {
  await limiter.wait();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const resp = await fetch(url, { signal: controller.signal });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return await resp.json();
  } finally {
    clearTimeout(timer);
  }
}

function eutilsUrl(endpoint, params) {
  const p = new URLSearchParams({ ...params, tool: "corpus-checker" });
  return `${EUTILS_BASE}${endpoint}?${p.toString()}`;
}

async function esearch(db, term) {
  return fetchJson(eutilsUrl("esearch.fcgi", { db, term, retmode: "json" }));
}

async function esummary(db, ids) {
  return fetchJson(eutilsUrl("esummary.fcgi", { db, id: ids.join(","), retmode: "json" }));
}

async function elink(dbfrom, db, id) {
  return fetchJson(eutilsUrl("elink.fcgi", { dbfrom, db, id, retmode: "json" }));
}

// --------------------------------------------------------------------------- crosswalk.py port (GEO/PubMed/BioStudies)

function parseGdsEntry(entry) {
  const relations = entry.extrelations || [];
  const sraRelation = relations.find((r) => r.relationtype === "SRA");
  return {
    entrytype: entry.entrytype || null,
    accession: entry.accession || null,
    pmids: entry.pubmedids || [],
    bioproject: entry.bioproject || null,
    sra: sraRelation ? sraRelation.targetobject : null,
  };
}

function parsePubmedEntry(entry) {
  const ids = {};
  for (const a of entry.articleids || []) {
    if (a.idtype) ids[a.idtype] = a.value;
  }
  return { doi: ids.doi || null };
}

function elinkTargets(link) {
  const linksets = link.linksets || [];
  if (!linksets.length) return [];
  const dbs = linksets[0].linksetdbs || [];
  return dbs.length ? (dbs[0].links || []) : [];
}

function dedup(items) {
  return [...new Set(items.filter((x) => x !== null && x !== undefined && x !== ""))];
}

async function resolveGeo(accession) {
  const prefix = accession.slice(0, 3);
  const searchRes = await esearch("gds", `${accession}[ACCN]`);
  const idlist = (searchRes.esearchresult && searchRes.esearchresult.idlist) || [];
  if (!idlist.length) throw new Error(`no GEO record found for ${accession}`);

  const summary = await esummary("gds", idlist);
  const entries = Object.entries(summary.result || {})
    .filter(([uid]) => uid !== "uids")
    .map(([, e]) => parseGdsEntry(e));
  const match = entries.find((e) => e.entrytype === prefix);
  if (!match) throw new Error(`esearch found ${accession} but no esummary result has entrytype=${prefix}`);

  let pmids = match.pmids;
  if (!pmids.length && prefix === "GSM") {
    // A sample record rarely carries its own PMID; fall back to its parent series, already
    // in this same batch (esearch for a GSM returns the parent GSE's uid alongside it).
    const parent = entries.find((e) => e.entrytype === "GSE");
    if (parent) pmids = parent.pmids;
  }

  const accessions = dedup([accession, match.bioproject, match.sra]);
  let doi = null;
  if (pmids.length) {
    const pubmed = await esummary("pubmed", [pmids[0]]);
    doi = parsePubmedEntry(pubmed.result[pmids[0]]).doi;
  }
  return { accessions, pmids, doi };
}

async function resolvePmidCore(pmid) {
  const pubmed = await esummary("pubmed", [pmid]);
  const article = parsePubmedEntry(pubmed.result[pmid]);

  const link = await elink("pubmed", "gds", pmid);
  const gdsUids = elinkTargets(link);

  let accessions = [];
  if (gdsUids.length) {
    const gds = await esummary("gds", gdsUids);
    const series = Object.entries(gds.result || {})
      .filter(([uid]) => uid !== "uids")
      .map(([, e]) => parseGdsEntry(e))
      .filter((s) => s.entrytype === "GSE");
    for (const s of series) accessions.push(s.accession, s.bioproject, s.sra);
  }
  return { accessions: dedup(accessions), pmids: [pmid], doi: article.doi };
}

async function resolveDoi(doi) {
  const searchRes = await esearch("pubmed", `"${doi}"[DOI]`);
  const idlist = (searchRes.esearchresult && searchRes.esearchresult.idlist) || [];
  if (!idlist.length) throw new Error(`DOI ${doi} did not resolve to a PubMed record`);
  return resolvePmidCore(idlist[0]);
}

function biostudiesAttr(section, name) {
  for (const a of section.attributes || []) {
    if (a.name === name) return a.value;
  }
  return null;
}

/** EBI BioStudies: an ArrayExpress accession's publication PMID/DOI. Not an NCBI endpoint. */
async function resolveArrayExpress(accession) {
  const doc = await fetchJson(`${BIOSTUDIES_BASE}${accession}`);
  const section = doc.section || {};
  let pmid = null;
  let doi = null;
  for (const sub of section.subsections || []) {
    if (sub && sub.type === "Publication") {
      const accno = String(sub.accno || "");
      if (/^\d+$/.test(accno)) pmid = accno;
      doi = biostudiesAttr(sub, "DOI") || doi;
    }
  }
  return { accessions: [accession], pmids: pmid ? [pmid] : [], doi };
}

async function resolveToken(token) {
  const s = token.trim();
  if (GEO_RE.test(s)) return resolveGeo(s.toUpperCase());
  if (ARRAYEXPRESS_RE.test(s)) return resolveArrayExpress(s.toUpperCase());
  if (DOI_RE.test(s)) return resolveDoi(s.toLowerCase());
  if (PMID_RE.test(s)) return resolvePmidCore(s);
  throw new Error(`unrecognised identifier ${token}`);
}

// --------------------------------------------------------------------------- lookup.py: interpret() port

function queryFromCatalog(catalogId, raw, typed, notes, catalog) {
  const d = catalog[catalogId] || { identifiers: [] };
  const identifiers = (d.identifiers || []).map((i) => [i[0], i[1]]);
  const provisional = (d.identifiers || [])
    .filter((i) => i.length > 2 && i[2] === "provisional")
    .map((i) => [i[0], i[1], i[3] || ""]);
  return [{
    raw, kind: "dataset", identifiers, typed, catalog_id: catalogId,
    linked_from: null, notes, provisional,
  }];
}

/** One line of input -> one or more queries. Mirrors lookup.interpret(). Network by default. */
export async function interpretQuery(raw, loaded, { network = true } = {}) {
  const { catalog } = loaded;

  const topCatalogId = catalogMatch(raw, new Set(), catalog);
  if (topCatalogId) return queryFromCatalog(topCatalogId, raw, [topCatalogId], [], catalog);

  const tokens = parse(raw);
  const typed = [...tokens.accession, ...tokens.pmid, ...tokens.doi];
  if (!typed.length) {
    return [{
      raw, kind: "unrecognised", identifiers: [], typed: [], catalog_id: null,
      linked_from: null, notes: ["No accession, PMID or DOI found in this input."], provisional: [],
    }];
  }

  const notes = [];
  const accessions = new Set(tokens.accession);
  const pmids = new Set(tokens.pmid);
  const dois = new Set(tokens.doi);
  const linked = new Set();

  if (network) {
    for (const token of typed) {
      if (!NCBI_RESOLVABLE.test(token)) continue; // CELLxGENE UUIDs, SRA/BioProject/EGA ids: as entered
      let rec;
      try {
        rec = await resolveToken(token);
      } catch (err) {
        notes.push(`Could not expand ${token} (${err && err.name ? err.name : "Error"}); matched only on what was entered.`);
        continue;
      }
      for (const p of rec.pmids) pmids.add(p);
      if (rec.doi) dois.add(String(rec.doi).trim().toLowerCase());
      if (token.toUpperCase().startsWith("GSE")) {
        for (const a of rec.accessions) accessions.add(a);
      } else if (tokens.pmid.includes(token) || tokens.doi.includes(token)) {
        for (const a of rec.accessions) {
          if (String(a).toUpperCase().startsWith("GSE")) linked.add(a);
        }
      }
      // A GSM is one sample: never widen it to its series, or sibling samples would match.
    }
  }

  if (tokens.accession.length) {
    const catalogId = catalogMatch(raw, accessions, catalog);
    if (catalogId) return queryFromCatalog(catalogId, raw, typed, notes, catalog);
    const identifiers = [
      ...[...accessions].sort().map((a) => [accessionType(a), a]),
      ...[...pmids].sort().map((p) => ["pmid", p]),
      ...[...dois].sort().map((d) => ["doi", d]),
    ];
    return [{
      raw, kind: "dataset", identifiers, typed, catalog_id: null,
      linked_from: null, notes, provisional: [],
    }];
  }

  // A paper: answer at the paper level, then once per dataset it links to.
  const paper = {
    raw, kind: "paper",
    identifiers: [...[...pmids].sort().map((p) => ["pmid", p]), ...[...dois].sort().map((d) => ["doi", d])],
    typed, catalog_id: null, linked_from: null, notes, provisional: [],
  };
  const queries = [paper];
  for (const gse of [...linked].sort()) {
    const sub = await interpretQuery(gse, loaded, { network });
    for (const q of sub) queries.push({ ...q, linked_from: raw });
  }
  return queries;
}

// --------------------------------------------------------------------------- DOM wiring

function verdictSlug(verdict) {
  return verdict.toLowerCase().replaceAll(" ", "-");
}

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k === "html") node.innerHTML = v;
    else node.setAttribute(k, v);
  }
  for (const c of [].concat(children)) {
    if (c) node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return node;
}

function verdictChip(a) {
  const chip = el("span", { class: `verdict v-${verdictSlug(a.verdict)}` }, short(a.verdict));
  const wrap = el("span", { class: "lookup-verdict" }, [chip]);
  if (a.identity === "provisional") {
    const basis = a.identity_basis ? a.identity_basis.trim() : "basis not recorded";
    wrap.appendChild(el("span", {
      class: "identity-badge",
      title: `Provisional identity: ${basis}`,
    }, "provisional identity"));
  }
  return wrap;
}

function config() {
  const cfg = window.CORPUS_CHECKER_LOOKUP || {};
  return {
    indexUrl: cfg.indexUrl || "lookup-index.json",
    repoUrl: cfg.repoUrl || "",
    rootPrefix: cfg.rootPrefix || "",
  };
}

function buildAddDatasetUrl(repoUrl, query, answers) {
  const base = `${repoUrl}/issues/new?template=add-dataset.yml`;
  const title = `[add-dataset] ${query.raw}`;
  const idLines = [query.raw, ...query.identifiers.map(([t, v]) => identifierLabel(t, v))];
  const identifiers = [...new Set(idLines)].join("\n");
  const results = answers
    .map((a) => `${a.model_name} (${a.stage}): ${short(a.verdict)} — ${answerText(a, { lead: false })}`)
    .join("\n");
  const params = new URLSearchParams({ title, identifiers, "lookup-results": results });
  return `${base}&${params.toString()}`;
}

function answerDetail(a, query) {
  const parts = [];
  parts.push(el("p", { class: "lookup-sentence" }, answerText(a, { lead: false })));
  const origin = a.recorded ? "recorded finding" : "looked up here";
  parts.push(el("p", { class: "muted lookup-provenance" }, `${origin} · ${provenanceText(a)}`));
  if (a.match_level === "publication") {
    parts.push(el("p", { class: "pub-warning" },
      "Matched on the paper, not the dataset: confirm it is the same dataset before relying on this."));
  }
  const also = query.identifiers.filter(([, v]) => !query.typed.includes(v) && v !== query.catalog_id);
  if (also.length) {
    const label = also.map(([t, v]) => identifierLabel(t, v)).join(", ");
    parts.push(el("p", { class: "muted also-searched" }, `also searched: ${label}`));
  }
  return parts;
}

function renderModelCard(a, query) {
  const header = el("div", { class: "lookup-card-header" }, [
    el("span", { class: "lookup-model-name" }, a.model_name + (a.draft ? " " : "")),
    a.draft ? el("span", { class: "draft-badge" }, "Draft — not yet verified") : null,
    verdictChip(a),
  ]);
  const card = el("li", { class: "lookup-card", id: `lookup-${query.raw}-${a.model}-${a.stage}` }, [header, ...answerDetail(a, query)]);
  return card;
}

function coverageLine(searched, includesDrafts) {
  const n = searched.length;
  const noun = n === 1 ? "entry" : "entries";
  return `Searched ${n} ${noun}: ${searched.join(", ") || "—"}`
    + `${includesDrafts ? " (including drafts)" : " (verified only)"}. Models not in the registry are not covered.`;
}

function renderSingleResult(container, query, result, loaded) {
  const section = el("section", { class: "lookup-query-block" });
  const heading = el("h2", { class: "lookup-query-heading" }, query.catalog_id
    ? el("a", { href: `${config().rootPrefix}datasets/${query.catalog_id}.html` }, query.raw)
    : query.raw);
  section.appendChild(heading);

  if (query.linked_from) {
    section.appendChild(el("p", { class: "muted" }, `linked from ${query.linked_from}`));
  }
  if (query.kind === "paper") {
    section.appendChild(el("p", { class: "muted" }, "a paper, not a dataset"));
  }
  for (const note of query.notes || []) {
    section.appendChild(el("p", { class: "muted lookup-note" }, note));
  }

  if (query.kind === "unrecognised") {
    section.appendChild(el("p", {}, "No accession, PMID or DOI found in this input."));
  } else if (!result.answers.length) {
    section.appendChild(el("p", {}, "No registered models to search."));
  } else {
    const list = el("ul", { class: "lookup-cards" });
    for (const a of result.answers) list.appendChild(renderModelCard(a, query));
    section.appendChild(list);
  }

  section.appendChild(el("p", { class: "muted coverage-line" },
    coverageLine(result.searched, loaded.__includeDrafts)));

  container.appendChild(section);
}

function renderListResult(container, queries, results, loaded) {
  const models = [];
  const seen = new Set();
  for (const r of results) {
    for (const a of r.answers) {
      if (!seen.has(a.model)) { seen.add(a.model); models.push({ id: a.model, name: a.model_name }); }
    }
  }

  const wrap = el("div", { class: "lookup-table-scroll" });
  const table = el("table", { class: "lookup-table" });
  const thead = el("thead", {}, el("tr", {}, [
    el("th", { scope: "col" }, "Dataset"),
    ...models.map((m) => el("th", { scope: "col" }, m.name)),
  ]));
  table.appendChild(thead);

  const tbody = el("tbody");
  queries.forEach((query, i) => {
    const result = results[i];
    const byModel = new Map(result.answers.map((a) => [a.model, a]));
    const rowHead = query.catalog_id
      ? el("a", { href: `${config().rootPrefix}datasets/${query.catalog_id}.html` }, query.raw)
      : query.raw;
    const row = el("tr", {}, [el("th", { scope: "row" }, rowHead)]);
    for (const m of models) {
      const a = byModel.get(m.id);
      if (!a) { row.appendChild(el("td", {}, "—")); continue; }
      const details = el("details", { class: "lookup-cell" });
      details.appendChild(el("summary", {}, [verdictChip(a)]));
      const detail = el("div", { class: "lookup-cell-detail" }, answerDetail(a, query));
      details.appendChild(detail);
      row.appendChild(el("td", {}, details));
    }
    tbody.appendChild(row);
  });
  table.appendChild(tbody);
  wrap.appendChild(table);
  container.appendChild(wrap);

  const searched = results.length ? results[0].searched : [];
  container.appendChild(el("p", { class: "muted coverage-line" }, coverageLine(searched, loaded.__includeDrafts)));

  // CSV export state for the "Copy as CSV" button.
  window.__lookupCsvRows = [
    ["dataset", ...models.map((m) => m.name)],
    ...queries.map((query, i) => {
      const result = results[i];
      const byModel = new Map(result.answers.map((a) => [a.model, a]));
      return [query.raw, ...models.map((m) => {
        const a = byModel.get(m.id);
        return a ? `${short(a.verdict)}${a.identity === "provisional" ? " (provisional identity)" : ""}` : "";
      })];
    }),
  ];
}

export function csvEscape(value) {
  const s = String(value ?? "");
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function toCsv(rows) {
  return rows.map((row) => row.map(csvEscape).join(",")).join("\n");
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

// --------------------------------------------------------------------------- page bootstrap

function parseListInput(text) {
  return text.split("\n")
    .map((line) => line.split("#", 1)[0].trim())
    .filter(Boolean);
}

async function main() {
  const cfg = config();
  const form = document.getElementById("lookup-form");
  const modeSingle = document.getElementById("mode-single");
  const singleFields = document.getElementById("single-mode-fields");
  const listFields = document.getElementById("list-mode-fields");
  const queryInput = document.getElementById("query-input");
  const queryList = document.getElementById("query-list");
  const statusEl = document.getElementById("lookup-status");
  const resultsEl = document.getElementById("lookup-results");
  const bannerEl = document.getElementById("paper-query-banner");
  const addDatasetLink = document.getElementById("add-dataset-link");
  const copyCsvBtn = document.getElementById("copy-csv");

  function syncMode() {
    const single = modeSingle.checked;
    singleFields.hidden = !single;
    listFields.hidden = single;
  }
  form.querySelectorAll('input[name="mode"]').forEach((r) => r.addEventListener("change", syncMode));
  syncMode();

  let loaded = null;
  let indexError = null;
  try {
    const resp = await fetch(cfg.indexUrl);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const json = await resp.json();
    loaded = loadIndex(json);
    loaded.__includeDrafts = true; // lookup-index.json always includes drafts; the site labels them
  } catch (err) {
    indexError = err;
  }

  const params = new URLSearchParams(window.location.search);
  const initialQ = params.get("q");
  const initialList = params.get("list");
  if (initialList) {
    document.getElementById("mode-list").checked = true;
    queryList.value = initialList;
    syncMode();
  } else if (initialQ) {
    queryInput.value = initialQ;
  }

  async function runLookup(lines, modelFilter) {
    resultsEl.innerHTML = "";
    bannerEl.hidden = true;
    copyCsvBtn.hidden = true;
    if (indexError) {
      statusEl.textContent = "Could not load the search index — try reloading the page.";
      return;
    }
    if (!lines.length) {
      statusEl.textContent = "Type at least one identifier.";
      return;
    }
    statusEl.textContent = "Searching…";

    const filtered = modelFilter.length
      ? { ...loaded, corpora: loaded.corpora.filter((c) => modelFilter.includes(c.model)) }
      : loaded;
    filtered.__includeDrafts = loaded.__includeDrafts;

    let network = true;
    let ncbiFailed = false;
    const allQueries = [];
    const allResults = [];
    let paperLevel = false;
    let lastAnswers = [];
    let lastQuery = null;

    for (const raw of lines) {
      const queries = await interpretQuery(raw, filtered, { network });
      for (const query of queries) {
        if (query.notes && query.notes.some((n) => n.startsWith("Could not expand"))) ncbiFailed = true;
        const result = search(query, filtered);
        allQueries.push(query);
        allResults.push(result);
        paperLevel = paperLevel || result.paperLevelWarning;
        lastAnswers = result.answers;
        lastQuery = query;
      }
    }

    if (ncbiFailed) {
      statusEl.textContent = "Couldn't reach NCBI — matched only on what you entered.";
    } else {
      statusEl.textContent = `Done — ${allQueries.length} ${allQueries.length === 1 ? "query" : "queries"}.`;
    }

    if (paperLevel) {
      bannerEl.hidden = false;
      bannerEl.textContent = loaded.disclaimer;
    }

    if (allQueries.length === 1) {
      renderSingleResult(resultsEl, allQueries[0], allResults[0], filtered);
    } else {
      renderListResult(resultsEl, allQueries, allResults, filtered);
      copyCsvBtn.hidden = false;
    }

    if (lastQuery && cfg.repoUrl) {
      addDatasetLink.href = buildAddDatasetUrl(cfg.repoUrl, lastQuery, lastAnswers);
    }
  }

  form.addEventListener("submit", (evt) => {
    evt.preventDefault();
    const single = modeSingle.checked;
    const modelFilter = [...form.querySelectorAll('input[name="model"]:checked')].map((i) => i.value);
    const lines = single ? [queryInput.value.trim()].filter(Boolean) : parseListInput(queryList.value);

    const url = new URL(window.location.href);
    url.searchParams.delete("q");
    url.searchParams.delete("list");
    if (single) {
      if (queryInput.value.trim()) url.searchParams.set("q", queryInput.value.trim());
    } else if (queryList.value.trim()) {
      url.searchParams.set("list", queryList.value);
    }
    window.history.replaceState(null, "", url);

    runLookup(lines, modelFilter);
  });

  copyCsvBtn.addEventListener("click", async () => {
    const rows = window.__lookupCsvRows;
    if (!rows) return;
    const ok = await copyText(toCsv(rows));
    copyCsvBtn.textContent = ok ? "Copied!" : "Copy failed";
    setTimeout(() => { copyCsvBtn.textContent = "Copy as CSV"; }, 2000);
  });

  if (initialQ || initialList) {
    form.dispatchEvent(new Event("submit", { cancelable: true }));
  }
}

// Guarded so this module can be imported under Node (no `document`) to exercise its pure
// exports (interpretQuery, RateLimiter, csvEscape, toCsv) without side effects; unchanged in
// a real browser, where `document` always exists.
if (typeof document !== "undefined") {
  main();
}
