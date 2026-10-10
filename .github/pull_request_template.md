<!--
Thanks for contributing to the registry. This checklist exists because every merged entry makes a public claim
about someone else's paper — docs/RULES.md and docs/ADDING_A_MODEL.md explain why each item here matters.
CI (.github/workflows/validate.yml) re-checks the mechanical parts: hashes, re-running every finding, every rule.
The rest needs a person to have actually looked: that's the review (docs/REVIEWING.md).
-->

## What this PR does

<!-- One or two sentences: new model entry, correction, dispute record, etc. -->

## Contributor checklist

- [ ] Ran `corpus-checker validate --allow-draft` locally, and the **only** remaining item is **G10** (the entry is
      Unreviewed — that one is for the reviewer, not you).
- [ ] The `review:` block is left **empty** (`by: ""`). Code and contributors never fill this in — only a person
      listed in `reviewers.yaml`, with `corpus-checker review`.
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
- [ ] If this PR touches an existing entry's `corpora` or `evaluated_on`, I know CI will require someone to review
      it again before it can merge — that's G03, not a bug in the workflow.

## Judgment calls made in this PR

<!--
List them here even if the checklist above already gestures at them. Be specific — "classified as Type A because
the supplementary file list includes a table titled X, which the availability statement itself never mentions" is
useful; "looks fine" is not.
-->

-

---

## For the reviewer

*What a review covers and how to record it: [docs/REVIEWING.md](https://github.com/leahmatzat/corpus-checker/blob/main/docs/REVIEWING.md). CI has already checked the hashes, re-run every finding and applied every rule; your job is what it can't check.*

- [ ] **Paper:** the published version, with the right DOI and PMID.
- [ ] **Manifest:** for each training stage, the listed file or statement is the paper's training-data list, and the
      quote matches the paper (read against the source, not just this diff).
- [ ] **Evaluation list:** the reported evals match what the paper evaluates on.
- [ ] **Judgment calls:** each one flagged above, and each provisional identity, recorded absence, identity note and
      caveat, reads right — or I changed it.
- [ ] **Plausibility:** the results make sense; nothing surprising goes unexplained.
- [ ] I recorded the review myself with `corpus-checker review <id>`, ticking only what I actually looked at.
