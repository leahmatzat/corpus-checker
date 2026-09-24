# Deploying the static site

*For the repo owner. You do not need to know GitHub Actions or Hugging Face's internals to do
this — follow the steps in order.*

The site is a **static** build generated from `registry/` and `datasets/` on every merge to
`main` (`.github/workflows/deploy-space.yml`) and pushed to a Hugging Face Space. **GitHub stays
the source of truth** — the Space is regenerated, never hand-edited, and every page links back
here for corrections. Nothing described here changes that; it just gets the already-validated
site onto a page people can browse.

> ⚠ **Never paste a token into an issue, a PR, a chat message, or a file in this repo — including
> a commit, even a private-looking one.** The one place it belongs is the GitHub Actions secret
> described below. If you ever paste one somewhere else by accident, revoke it on
> huggingface.co/settings/tokens immediately and issue a new one.

---

## One-time setup

### 1. Create the Space (or let the first deploy create it)

Go to [huggingface.co/new-space](https://huggingface.co/new-space) and create a Space with:
- **SDK: Static**
- any name, e.g. `corpus-checker`

You can skip this step — `scripts/deploy_space.py` calls `create_repo(..., exist_ok=True)`, so
the first real deploy creates the Space automatically if it doesn't exist yet. Creating it by
hand first just lets you see it before anything is pushed.

### 2. Create a fine-grained token scoped to only that Space

Go to [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) → **Create new
token** → **Fine-grained**. Grant it **write access to this Space only** — not to your whole
account, not to other repos. This limits the damage if the token ever leaks.

### 3. Add the token as a GitHub Actions secret

In the GitHub repo: **Settings → Secrets and variables → Actions → New repository secret**.

- Name: `HF_TOKEN`
- Value: the token from step 2

The workflow reads this as `secrets.HF_TOKEN` and passes it to `scripts/deploy_space.py` only
through the environment (`HF_TOKEN=...`) — it is never written to a log or a command line.

### 4. Set the target Space as a repository variable

Same location, but the **Variables** tab: **Settings → Secrets and variables → Actions →
Variables → New repository variable**.

- Name: `HF_SPACE_ID`
- Value: `your-username/corpus-checker` (the Space's full id, as it appears in its URL)

If this variable is unset and no `space_id` is given manually (next section), the deploy
workflow exits successfully with a notice — it never fails CI just because deployment isn't
configured yet.

### 5. Run the workflow manually first, with `dry_run`

Before trusting it to run unattended on every merge: **Actions → deploy-space → Run workflow**,
and set `dry_run: true`. This lists what would be uploaded without contacting Hugging Face at
all — no token is even required for that path. Once that looks right, run it again with
`dry_run: false`, optionally first against a separate **test** Space by filling in the `space_id`
input (this overrides `HF_SPACE_ID` for that one run) — useful for checking a real deploy without
touching the Space the public sees.

After that, every push to `main` deploys automatically.

---

## Branch protection (do this too)

The deploy workflow trusts that anything reaching `main` already passed the guards. That trust
only holds if `main` is actually protected:

**Settings → Branches → Add branch protection rule** for `main`:
- Require status checks to pass before merging → select the `guards` job from the `validate`
  workflow (`.github/workflows/validate.yml`) — this is what runs the schema, the G00–G20 guards,
  and the stale-verification check (G03). *GitHub only lists a check here after it has run once,
  so open the first pull request before setting this.*
- Require a pull request before merging, with **at least one approving review from a code owner**.
- ⚠ **Leave "Do not allow bypassing the above settings" unticked while you are the only verifier.**
  GitHub never lets you approve your own pull request, so a strict rule would stop you merging
  your own changes. Bypass lets repository admins (you) merge their own PRs once the checks pass;
  everyone else still needs your review. Revisit this when a second verifier joins.

### CODEOWNERS

`.github/CODEOWNERS` makes `@leahmatzat` the required reviewer for everything that carries the
registry's claims — `registry/`, `datasets/`, `snapshots/`, `schema/`, `verifiers.yaml` and the
workflows themselves. It only binds once branch protection's "require review from a code owner"
is on — without it, a contributor could approve (or re-sign) their own change to a verdict.
Add a second verifier here and in `verifiers.yaml` in the same reviewed PR.

---

## Running it again later

Nothing further is required day to day — every merge to `main` deploys on its own. To push a
one-off deploy without merging (e.g. after changing something deploy-related), use **Actions →
deploy-space → Run workflow** the same way as step 5 above.
