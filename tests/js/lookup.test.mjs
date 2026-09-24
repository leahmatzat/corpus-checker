// Replays tests/fixtures/lookup_vectors.json — generated from the Python engine — against the
// browser's lookup-core.mjs. If this fails, lookup-core.mjs disagrees with lookup.py; fix the JS,
// never the vectors file (it is off limits — see scripts/make_lookup_vectors.py).
//
// No network: `lookup` vectors need a prebuilt lookup-index.json per include_drafts setting,
// supplied via LOOKUP_INDEX_VERIFIED / LOOKUP_INDEX_DRAFTS env vars (tests/test_lookup_js.py sets
// these before invoking `node --test tests/js/`).
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

import {
  parse, accessionType, decideFrom, loadIndex, search,
} from "../../src/corpus_checker/site_templates/lookup-core.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const VECTORS_PATH = path.resolve(HERE, "..", "fixtures", "lookup_vectors.json");
const vectors = JSON.parse(readFileSync(VECTORS_PATH, "utf-8"));

test("parse() matches every vector", () => {
  for (const c of vectors.parse) {
    assert.deepEqual(parse(c.input), c.expected, `parse(${JSON.stringify(c.input)})`);
  }
});

test("accessionType() matches every vector", () => {
  for (const c of vectors.accession_type) {
    assert.equal(accessionType(c.input), c.expected, `accessionType(${JSON.stringify(c.input)})`);
  }
});

test("decideFrom() matches verdict + code for every vector", () => {
  for (const c of vectors.decide) {
    const d = decideFrom(c.input);
    assert.equal(d.verdict, c.expected.verdict, `${c.name}: verdict`);
    assert.equal(d.code, c.expected.code, `${c.name}: code`);
  }
});

test("every verdict code is covered by the vectors (sanity)", () => {
  const codes = new Set(vectors.decide.map((c) => c.expected.code));
  const expectedCodes = new Set([
    "no_manifest", "unconfirmed_source", "present", "superset", "same_publication",
    "weak_only", "unsearched", "no_exact_key", "missing_required_key", "not_present",
  ]);
  assert.deepEqual(codes, expectedCodes);
});

// -------------------------------------------------------------------- lookup vectors (needs an index)

const indexPaths = {
  false: process.env.LOOKUP_INDEX_VERIFIED,
  true: process.env.LOOKUP_INDEX_DRAFTS,
};

function loadedFor(includeDrafts) {
  const p = indexPaths[String(includeDrafts)];
  if (!p) {
    throw new Error(
      `LOOKUP_INDEX_${includeDrafts ? "DRAFTS" : "VERIFIED"} is not set — run via tests/test_lookup_js.py, `
      + "which builds both index files first.",
    );
  }
  return loadIndex(JSON.parse(readFileSync(p, "utf-8")));
}

function queryFor(c) {
  const identifiers = c.identifiers.map((i) => [i[0], i[1]]);
  const provisional = c.identifiers
    .filter((i) => i.length > 2 && i[2] === "provisional")
    .map((i) => [i[0], i[1], i[3]]);
  return {
    raw: c.name,
    kind: c.kind,
    identifiers,
    typed: identifiers.map(([, v]) => v),
    catalog_id: c.catalog_id,
    linked_from: null,
    notes: [],
    provisional,
  };
}

test("search() matches every lookup vector (verified-only and with drafts)", () => {
  const loaded = { false: loadedFor(false), true: loadedFor(true) };
  for (const c of vectors.lookup) {
    const result = search(queryFor(c), loaded[String(c.include_drafts)]);
    const label = `${c.name} (include_drafts=${c.include_drafts})`;

    assert.equal(result.paperLevelWarning, c.expected.paper_level_warning, `${label}: paper_level_warning`);
    assert.equal(result.answers.length, c.expected.answers.length, `${label}: answer count`);

    result.answers.forEach((a, i) => {
      const exp = c.expected.answers[i];
      assert.equal(a.model, exp.model, `${label}[${i}]: model`);
      assert.equal(a.stage, exp.stage, `${label}[${i}]: stage`);
      assert.equal(a.verdict, exp.verdict, `${label}[${i}]: verdict`);
      assert.equal(a.code, exp.code, `${label}[${i}]: code`);
      assert.equal(a.match_level, exp.match_level, `${label}[${i}]: match_level`);
      assert.deepEqual(a.keys_attempted, exp.keys_attempted, `${label}[${i}]: keys_attempted`);
      assert.deepEqual(a.matched_on, exp.matched_on, `${label}[${i}]: matched_on`);
      assert.equal(a.samples, exp.samples, `${label}[${i}]: samples`);
      assert.equal(a.cells, exp.cells, `${label}[${i}]: cells`);
      assert.equal(a.recorded, exp.recorded, `${label}[${i}]: recorded`);
      assert.equal(a.identity, exp.identity, `${label}[${i}]: identity`);
    });
  }
});
