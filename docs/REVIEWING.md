# Reviewing an entry

How to review a registry entry, what a review covers, and what to do about each problem the validator can report. For the rules themselves, see [RULES.md](RULES.md).

Run every command from the repository folder. `.venv/bin/corpus-checker` is the tool installed there (see the README's "Run it").

---

## 1. What a review means

**A review means a named person looked at what the tool can't check, and found nothing wrong.** It is not an independent replication. Nobody redoes the search by hand, because the tool already does the mechanical checks, and does them on every change:

| Checked by the tool | How |
|---|---|
| Each manifest file is the file the publisher serves | Its SHA-256 matches the publisher's download; the downloads are re-checked weekly (G04) |
| Every finding comes out of the manifest | Each corpus whose manifest is stored in the repo is re-run on every change, and must match what's recorded (G26) |
| The entry follows the rules | Every guard in [RULES.md](RULES.md#guard-codes) runs on every pull request |
| Dataset identifiers are real | Each is confirmed against NCBI or the dataset's archive, or marked provisional |

What only a person can do are these five checks. A review records which of them it covered, and the model's page shows them under the reviewer's name. Anything left out shows as **not checked yet** (G25).

| Check | What you look at |
|---|---|
| `paper` | The paper is the published version, with the right DOI and PMID. |
| `manifest` | For each training stage, the listed file or statement is the paper's training-data list, and the quote matches the paper. |
| `evaluated_on` | The reported evals match what the paper evaluates on. |
| `judgment_calls` | Each judgment call reads right: provisional identities, recorded absences, identity notes and caveat wording. |
| `plausibility` | The results make sense; nothing surprising goes unexplained. |

Where we looked for a manifest is recorded separately, place by place, in the entry's `discovery` block. Any place not yet searched also shows as not checked yet (G18).

A review is accountable: the reviewer's name is on the page, and corrections and disputes come to them. If anyone later changes a verdict, a manifest or the evaluation list, the entry is blocked until someone reviews it again (G03), so a review always refers to the entry as it stands.

## 2. Find what needs you

```bash
.venv/bin/corpus-checker validate --base-ref main
```

This compares the branch with `main`. Each error names a guard code, a file and the exact field. The ones a reviewer acts on:

| Code | What it means | What you do |
|---|---|---|
| **G03** | A reviewed entry changed since its review | Review the change (section 3) |
| **G10** | `review.by` is empty: the entry is Unreviewed | Review it, fix it and then review it, or keep it out of this merge (section 4) |
| **G26** | A recorded finding no longer comes out of its manifest | Re-run `check <model> --rerun` and find out which side changed |
| **G14** | `NOT PRESENT` without every key the manifest needs (usually a missing accession or PMID) | Find and confirm the missing identifier in `datasets/`; or, if it genuinely doesn't exist, record that under `absent:` with the evidence and the date; or accept `INCONCLUSIVE` |
| **G09** | `NOT PRESENT` (or a `PRESENT` with no confirmed file to match in) rests on a supplied file nobody has confirmed | Confirm the file (publisher download matches its hash, totals match the paper, or the authors confirm), or accept `INCONCLUSIVE` |
| **G21** | `PRESENT` on a paper-level match (PMID/DOI only) | Add an `identity_note` saying why it is the same dataset, or accept `INCONCLUSIVE` |
| **G23** | A finding's provisional identity disagrees with the catalog | Make `identity` / `identity_basis` match the dataset's identifiers |
| **G24** | A finding's "none exists" keys disagree with the catalog, or a key recorded as not existing also has an identifier | Re-run `check <model> --rerun` and copy its `keys_absent`; if an identifier has turned up, remove the absence from `datasets/` |
| **G04** | A manifest re-downloaded from its URL no longer matches its hash | Download it, find out what changed, and re-check the entry |
| G05 *(warning)* | A manifest was last checked over a year ago | Re-check when convenient |
| G18 *(warning)* | `NOT PRESENT` while some places a manifest could live are unchecked | Check them, or accept the risk knowingly |
| G25 *(warning)* | The review didn't cover all five checks | Cover the rest in your next review, or leave it: the page says what wasn't checked |

Any other code is a problem with how the entry is written; the message says which field to fix. The full list is in [RULES.md](RULES.md#guard-codes).

## 3. Review an entry

Run the review command **in your own terminal**:

```bash
.venv/bin/corpus-checker review <id>
```

It walks you through the review:

1. **What changed** since `main` (or `--base-ref <ref>`), in the entry and the catalog.
2. **What the tool checked:** every recorded finding re-run from the stored manifest, and any difference. You don't need to redo this.
3. **The five checks**, one at a time. Answer `y` for each one you looked at yourself, `n` for any you didn't. Before the judgment-calls question it lists the entry's actual calls.
4. **Your initials**, which must be listed in [`reviewers.yaml`](../reviewers.yaml).
5. It writes `review.by`, `review.date` (today) and `review.checked` into the entry, changing nothing else, and prints the commands to check the change and commit it.

It refuses to run anywhere but a person's terminal (no scripts, CI or assistants), so code can never review an entry.

**To do the checks**, open the paper beside the entry. Useful views:

```bash
git --no-pager diff main <branch> -- registry/<id>.yaml datasets/
```

```bash
.venv/bin/corpus-checker check <id> --rerun --manifests ~/Downloads
```

The second re-runs every finding from the publisher's original files, which must match their recorded hashes. That's the strongest form of the tool's check, and useful when a manifest isn't stored in the repo.

**On GitHub, without a terminal:** a reviewer can also edit the entry's `review:` block on the pull request (**Files changed** → the file's **⋯** menu → **Edit file**), setting `by`, `date` and the `checked` list. The command is safer, because it can't overwrite anything else.

## 4. When you're not ready to review

Leave `review.by` empty. An Unreviewed entry can't merge to `main`, and it is never deleted silently. Either:

- **fix it and review it**, or
- **keep it out of this merge.** A maintainer moves the entry to a follow-up branch, so everything else can merge. It comes back when it's ready.

## 5. Adding another reviewer

Add them to [`reviewers.yaml`](../reviewers.yaml) and [`.github/CODEOWNERS`](../.github/CODEOWNERS) in one pull request, reviewed by an existing reviewer. Nobody adds themselves.
