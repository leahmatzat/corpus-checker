<!--
Thanks for contributing to the registry. This checklist exists because every merged entry makes a public claim
about someone else's paper — docs/RULES.md and docs/ADDING_A_MODEL.md explain why each item here matters.
CI (.github/workflows/validate.yml) re-checks the mechanical parts; the rest needs a human to have actually done it.
-->

## What this PR does

<!-- One or two sentences: new model entry, correction, dispute record, etc. -->

## Contributor checklist

- [ ] Ran `corpus-checker validate --allow-draft` locally, and the **only** remaining item is **G10** (empty
      `verified_by` — that one is for the verifier, not you).
- [ ] `provenance.verified_by` is left **empty**. Code and contributors never fill this in — only a human listed in
      `verifiers.yaml` signs it.
- [ ] `result.sources_searched` is populated on every finding that needs it (non-empty for any manifest type other
      than `C-vague` / `D`) — no `NOT PRESENT` without saying what was searched.
- [ ] The `discovery` block is filled in for every one of the nine locations, **including the ones where you found
      nothing** — an unchecked location is not the same as "nothing there."
- [ ] **Every judgment call is flagged explicitly below**, not resolved silently. In particular:
  - [ ] Manifest-type ambiguity (e.g. was this Type A or C-named? did you read the *whole* availability statement,
        and enumerate every supplementary file, before deciding?)
  - [ ] Any dataset that could plausibly be training data *or* evaluation data, and which way you read it
  - [ ] Any match that is title- or keyword-only (these must be `INCONCLUSIVE`, never `PRESENT` / `NOT PRESENT` —
        CI's G01 will block it if not)
- [ ] If this PR touches an existing entry's `corpora` or `evaluated_on`, I know CI will require a verifier to
      re-sign (`verified_date` updated) before it can merge — that's G03, not a bug in the workflow.

## Judgment calls made in this PR

<!--
List them here even if the checklist above already gestures at them. Be specific — "classified as Type A because
the supplementary file list includes a table titled X, which the availability statement itself never mentions" is
useful; "looks fine" is not.
-->

-

---

## For the verifier

*Step-by-step: [docs/VERIFYING.md](https://github.com/leahmatzat/corpus-checker/blob/main/docs/VERIFYING.md). Run `corpus-checker validate --base-ref main` and `corpus-checker check <id> --rerun` first.*

- [ ] I re-read the quoted availability statement(s) against the source, not just against this diff.
- [ ] I confirmed every `manifestSource` this PR relies on has a `confirmation.method` that actually supports the
      verdicts drawn from it (`none` caps everything at `INCONCLUSIVE` — G09).
- [ ] I checked `keys_attempted` on every `NOT PRESENT` finding against the manifest's `requires_keys` (G14) and
      that every attempted key is backed by a *confirmed* `datasets/` identifier (G15), not an assumed one.
- [ ] I checked every judgment call the contributor flagged above and either agree with it or changed it.
- [ ] I am listed in `verifiers.yaml`, and I am filling in `verified_by` / `verified_date` myself — not on someone
      else's behalf.
