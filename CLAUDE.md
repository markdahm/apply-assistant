# apply-assistant

## Which client is this checkout?

The engine runs **one candidate per checkout**, and there is more than one.
**Read `CLIENT.md` at this repo root before running anything.** It names the
candidate, their `CANDIDATE_EMAIL`, the database and the Desk URL for *this*
checkout. It is untracked, so unlike this file it cannot be wrong about which
one you are in.

**Since 12 September 2026 there is ONE Desk for every candidate.** One Vercel
project (`job-desk`), one private blob store, Google sign-in, and every blob
under a per-candidate prefix `c/<id>/` where `id = sha256(email)[:16]`. A
checkout no longer owns a project or a store; it owns a database, a profile,
and a `CANDIDATE_EMAIL` in `.env` that puts its publishes and fetches under the
right prefix (`apply_assistant/tenant.py`). The Desk derives the same id from
whoever signed in (`site/api/_lib/roster.mjs`); `tests/test_candidate_id.py`
drives both implementations and fails if they ever disagree.

Everything below describes the engine. Where it names a specific candidate or
state, that is **client 1** — `CLIENT.md` overrides it.

## Who can sign in, and what they see

- **`DESK_OPERATORS`** (Vercel env) — Mark. Sees every candidate on the roster,
  picks one from a menu in the masthead; the pick is a `desk_as` cookie the
  server honours only for roster ids.
- **`DESK_CANDIDATES`** (Vercel env) — the candidates, by Google address. Each
  sees only their own Desk. A forged `desk_as` is ignored for a candidate.
- Anyone on neither list is refused at the callback and again on every request:
  the address rides inside the signed session cookie, and the roster is re-read
  per request, so removing an address locks that person out immediately.
- An empty roster, a missing secret, or a missing client id **closes** the door.
- `api/me` reports who is signed in; every data function (`jobs`, `status`,
  `inbox`, `letter`, `onboard`) starts with `requireCandidate()` and answers
  401 (no session) or 409 (operator with nobody picked) before touching a blob.
- Onboarding files a submission under the SIGNED-IN candidate's prefix. The
  typed email goes on the resume; identity comes from the session.

## What this is

A job-search engine built for **one real person at a time**. Mark built it; the
first candidate is a friend of Mark's — Mark runs the pipeline, the candidate
reviews and clicks apply himself.

Repo: `github.com/markdahm/apply-assistant` (public, MIT). Python package plus a
Vercel-hosted review app called **The Desk** (project `job-desk`).

The pipeline: `sweep → enrich → match → tailor → letters → export → publish`.
Everything before the final click is automated; **the submit click always stays
with the human** — Firecrawl is read-only and never submits an application.

## Live deployment

- **URL:** https://job-desk-theta.vercel.app (Google sign-in; `/onboard` is the
  candidate form). Project `job-desk` under scope `mark-dahms-projects`,
  `prj_xqB6zvQBUrD68ohGy6pm5HXutRcF`. **Serves every candidate.**
- **Env vars the site reads:** `DESK_GOOGLE_CLIENT_ID` (not secret — it travels
  in the browser's URL bar), `DESK_GOOGLE_CLIENT_SECRET` (Sensitive),
  `DESK_SESSION_SECRET` (Sensitive, any long random string), `DESK_OPERATORS`,
  `DESK_CANDIDATES`, plus `BLOB_READ_WRITE_TOKEN` and `BLOB_STORE_ID`.
  `DESK_PASSWORD` is retired and unread. The OAuth client's redirect URI is
  `https://job-desk-theta.vercel.app/api/auth/callback/google` (derived from the
  request origin, so a localhost URI can be registered alongside).
- **Deploys are CLI-only — do NOT connect Git.** `site/onboard.html` and
  `site/desk.html` are gitignored generated files, so a Git-based build would
  ship a site with no onboarding form and no Desk. `./deploy.sh` generates them,
  runs the site tests, and uploads from the local machine.
- **Nothing candidate-specific ships as a static file.** `deploy.sh` always
  writes an EMPTY `desk-data.js`, and `site/.vercelignore` keeps the exported
  PDFs, `resume.html` and the screenshot-bearing `guide.html` out of the upload.
  A static file is served to everyone who can sign in; each person's data comes
  from `api/jobs` under their own prefix and PDFs render on demand via `api/pdf`.
  The Help button hides itself when `guide.html` is absent.
- **The blob store is PRIVATE** (`store_GyHmheg9ri8M3dBT`). That means:
  `access: 'private'` on every write, `@vercel/blob` **2.x** (0.27 predates
  private stores), and `Authorization: Bearer <token>` on every blob *read* —
  a private blob URL 403s without it. On the raw REST API used by `publish.py`,
  add `x-vercel-blob-access: private`.
- **`BLOB_READ_WRITE_TOKEN` is not auto-injected.** Connecting the store supplies
  `BLOB_STORE_ID` + OIDC only; the RW token was copied from the store's dashboard
  page and added with `vercel env add`. Locally it lives in `.env` at the repo
  root (pulled with `vercel env pull`) — note `--cwd site` writes it into `site/`,
  which is the deploy directory, so it must be moved to the root.

## Where things stand (5 August 2026)

- **Engine: complete and pushed.** All commands work.
- **Sources: verified live.** A full sweep pulled **7,289 jobs from 36 feeds,
  zero failures**, using only the free Greenhouse/Lever/Ashby JSON — no API keys.
  DB at `~/.apply-assistant/jobs.db`.
- **Remote onboarding: deployed and proven end to end.** A real browser
  submission went form → `api/onboard` → private blob → `apply onboard --check`
  → `fetch_and_save()`, with every field intact. The candidate fills the form at
  `/onboard`; Mark pulls it down with `apply onboard --fetch`.
- **The candidate has submitted, and has since edited his answers.** A `--fetch`
  on 5 August pulled the revised submission down cleanly. `config/profile.json`
  and `profile/*.md` all exist; `match`/`tailor`/`letters` have real material.
- **`.env` exists** at the repo root with `BLOB_READ_WRITE_TOKEN` — proven by
  `--fetch` reading the queue.

## Blockers before the candidate can start

**All clear as of 5 August 2026.** Both former blockers are resolved:

1. ~~`vercel login`~~ — the CLI is authenticated again (`vercel whoami` →
   `markdahm-2154`, CLI 58.7.1), and `site/.vercel/project.json` still points at
   `job-desk`. `site/deploy.sh` can deploy.
2. ~~`DESK_PASSWORD` / `BLOB_READ_WRITE_TOKEN`~~ — both present; the blob queue
   reads and writes fine.

## How it fits together

- **One form, two homes.** The onboarding form lives *only* in
  `apply_assistant/onboard.py`. The hosted copy at `site/onboard.html` is
  **generated** at deploy time (`apply onboard --emit-html`, called from
  `site/deploy.sh`) — never hand-edit it, and never commit it (gitignored).
  Both routes end in the same `save_all()`, so they produce identical files.
- **Two source lists, two owners.** `config/sources.json` belongs to the
  **candidate** — `--fetch` rebuilds it from their submission every time, so an
  employer added there by hand survives until the next fetch and then vanishes.
  `config/sources.extra.json` belongs to the **operator** and onboarding never
  writes it. `load_config()` unions the two at sweep time, de-duplicating so an
  employer named in both is fetched once; `sweep` prints how many merged, since
  a mistyped filename otherwise looks exactly like having no extras. A malformed
  extra file raises rather than being skipped — silently dropping every curated
  employer is the failure mode worth being loud about. Template:
  `config/sources.extra.example.json`. Covered by `tests/test_sources_merge.py`.
- **Free ATS feeds are per-company, and skew to tech.** There is no "search all
  of Greenhouse" — every employer needs its own slug, read off their careers
  URL. Probing 31 food and produce employers across Greenhouse/Lever/Ashby on
  5 Aug 2026 returned **2** live boards, both online grocery. For industries that
  don't use those systems, `firecrawl_boards` scrapes any careers page (needs
  `FIRECRAWL_API_KEY`, costs per scrape, and about half of hand-listed pages are
  JS portals that return nothing). Worth remembering that the two best-fitting
  employers found so far came from JSearch, which searches across employers
  nobody named.
- **Outside tech, the aggregator IS the channel.** Checked six Salinas Valley /
  Gilroy food employers on 5 Aug 2026: Church Brothers renders titles in HTML
  (scrapeable), Braga Fresh renders in HTML but had no openings, **Taylor Farms
  hosts no board at all** — its careers page links straight to LinkedIn — and
  **ofi/Olam** runs a SuccessFactors JS app whose own search reported no
  California openings. Produce and food-manufacturing employers mostly post to
  Indeed / LinkedIn / ZipRecruiter, which is exactly what JSearch aggregates.
  Build the query list, not the board list — the reverse of tech.
- **JSearch is tuned from `sources.extra.json`, and its defaults matter enormously.**
  `jsearch_pages` (default 2) and `jsearch_date_posted` (default `month`). Two
  settings were starving the feed:
  - `date_posted="week"` asks only for jobs posted in the last seven days, while
    a job board shows everything still open. One query returned **3** results on
    `week` and **10** with no date filter. Defensible for the Nth recurring
    sweep, wrong for the first — and the first is what forms the impression that
    the engine finds nothing.
  - The `/search-v2` migration dropped `page`/`num_pages` without replacing them,
    so every query silently returned one page regardless of config. v2 pages by
    opaque **cursor**. Restored 5 Aug; `tests/test_jsearch_paging.py` covers the
    stopping rules, because one page is one request.

  Fixing both took the queue from 16 survivors / 1 stretch to **27 / 9**, and the
  top score from 52 to 72. Budget is pages x queries: 8 phrases x 2 pages = 16
  requests per sweep, roughly 12 sweeps a month on the free tier.
- **Blob, not the Vercel CLI.** `publish.py` and `onboard.py` both talk to the
  Vercel Blob REST API directly with `BLOB_READ_WRITE_TOKEN`. That's deliberate:
  the pipeline host doesn't need the Vercel CLI at all.
- **One blob per item, never a shared array.** Both `api/inbox` and `api/onboard`
  write one object per submission. A shared array would need read-modify-write,
  which drops concurrent writes.
- **Step 3 of onboarding is gated, deliberately.** It was optional and skippable,
  which was the wrong default: `resume.md` is the fact source the tailor is
  allowed to draw from, and `voice_real.md` is the whole difference between a
  cover letter that sounds like the candidate and one that sounds like a robot.
  The form now blocks Finish without a resume (200+ chars) and at least one real
  writing sample, counts the samples live, and asks for three or four — sent mail
  is the easiest source. `save_all()` still keeps its placeholder fallbacks, since
  the local form and the API can be driven directly.

## Where this project lives

**Real path: `/Users/markdahm/OS/development/apply-assistant`.** No symlink — the
project sits directly in the OS folder alongside the other development projects.
It was folded back in on 11 August 2026.

### Why it was exiled, and why that no longer applies

It lived at `/Users/markdahm/apply-assistant` from 6 August 2026 because **macOS
TCC blocks launchd agents from reading anything under Desktop, Documents or
Downloads**, and the OS folder was on the Desktop at the time. The scheduled run
failed before it began:

```
shell-init: error retrieving current directory: getcwd: cannot access parent directories: Operation not permitted
/bin/bash: .../bin/scheduled.sh: Operation not permitted
```

Granting Terminal Full Disk Access does not help — a launchd agent is not
Terminal, and the process needing permission is `/bin/bash`. The whole OS folder
then moved to `/Users/markdahm/OS` on 7 August 2026, which removed the wall, so
the exile was no longer buying anything.

Verified on 11 August 2026 with a throwaway launchd probe agent — a real agent,
launched by launchd, successfully `cd`'d here, read `bin/scheduled.sh`, and ran
`.venv/bin/python3 -c 'import apply_assistant'`. Reasoning that TCC "should" be
fine is not the same as watching launchd do it.

**`~/Desktop/OS` is a symlink to `/Users/markdahm/OS`, and that symlink is
convenience, not a fix.** Traversing the Desktop path still touches the Desktop
directory entry, so the launchd agent must always be pointed at the real
`/Users/markdahm/OS/...` path. Never the Desktop one.

**Don't move any of it back under Desktop.** Anything scheduled will silently
stop, and the only evidence is `data/logs/launchd.err.log`.

### If this project ever moves again

`PROJECT_ROOT` uses `Path(__file__).resolve()`, and `bin/scheduled.sh` derives
its root with `cd "$(dirname "$0")/.."`, so the *code* relocates cleanly. Two
things do not:

1. **The venv.** 38 files bake in the absolute path — every console-script
   shebang in `.venv/bin/`, all four `activate` variants, and
   `__editable___apply_assistant_0_1_0_finder.py`, which is what makes
   `import apply_assistant` resolve. Either rebuild the venv or rewrite the old
   path to the new one across those files. Then test `apply` from **outside**
   this directory — run it from inside and the current directory shadows the
   editable install, so a broken install still looks fine.
2. **The launchd plist**, `~/Library/LaunchAgents/com.markdahm.apply-assistant.plist`,
   which hardcodes the path four times: the script, the working directory, and
   both log paths.

## Running the CLI on this Mac

**`apply` collides with macOS's own `/usr/bin/apply`.** Without the virtualenv
active, `apply sweep` runs the BSD `apply(1)` utility instead and fails with a
confusing `command not found: <first argument>`. Either activate the venv, or
call the entry point by path — which needs no activation:

```bash
/Users/markdahm/OS/development/apply-assistant/.venv/bin/apply <command>
```

Every bare `apply …` line in the README assumes an active venv.

## Tests

```bash
.venv/bin/python3 -m pytest
```

That is the whole suite. It runs the Python tests, drives the older script
suites through their own `main()`, and — via `tests/test_site_js.py` — runs
the Desk's five JavaScript suites in `site/tests/*.test.mjs` with `node --test`
(74 tests: session signing, Google sign-in and PKCE, the roster, the gate's
`decide()` with real cookies, and every API handler driven end to end with
`@vercel/blob` and `fetch` stubbed, including a full start → callback sign-in).
The bridge asserts the pass COUNT as well as the exit code, because
`node --test` exits 0 on zero files. `tests/test_candidate_id.py` runs the real
`roster.mjs` through node and compares ids with `tenant.py`. Run it from
anywhere; the bridges pin their working directories. `cd site && npm test` runs
the JavaScript half alone.

`pytest` is in the `dev` extra and is not installed by default. If pytest is
missing, `python3 -m unittest discover -s tests` reports `Ran 0 tests ... OK`,
which is a pass that ran nothing:

```bash
.venv/bin/python3 -m pip install -e ".[dev]"
```

### Why there is a bridge file

Only `tests/test_sources_merge.py` is written in pytest style. The other five
are standalone scripts with their own runner and a `main()` returning 1 on
failure, and their test functions are named for what they assert
(`walks_two_pages_by_default`) rather than `test_*`. pytest's default
`python_functions = test*` collects **nothing** from them.

Until 11 August 2026 that meant `pytest tests/` printed **"9 passed"** and
exited 0 while skipping 82 checks across five files. It was green, it was fast,
and it was testing about a ninth of what it claimed. `tests/test_script_suites.py`
now drives each script's `main()` and fails the run if any returns non-zero,
surfacing that suite's own FAIL lines.

**Adding a test file:** either name its functions `test_*` so pytest finds them
directly, or add the module to `SCRIPT_SUITES` in `tests/test_script_suites.py`.
`test_every_script_suite_is_registered` fails on any file that neither applies
to, so a new suite cannot go silently uncollected again.

Both harnesses still work standalone — `.venv/bin/python3 tests/test_payload_shape.py`
runs that one file and prints its own report.

## What the resume has to contain

Step 3 lists the categories and ticks them off live as the candidate pastes,
because a resume missing them fails *downstream*, expensively:

| Category | Why the pipeline needs it |
|---|---|
| Name, email, phone, city | Renders the resume header and the letter contact block |
| **Title, employer, dates per job** | **Hard requirement** — no parsed roles means `base_for_tailoring` returns `roles: []` and every tailoring attempt fails |
| Bullets per job | Tailoring is a 1:1 permutation of these; no bullets, nothing to permute |
| Numbers in the bullets | A cover letter may only cite figures that appear in the fact sources. A resume that says "grew revenue" without the number can never produce a letter that quotes it |
| Skills / competencies list | The tailor reorders and subsets it; it may not add to it |
| Education | Part of the rendered document and the fact base |

The detection is heuristic (date ranges, bullet-prefixed lines, an email or
phone, section keywords) and deliberately advisory — it never blocks submission,
it just makes a thin resume visible before it costs a failed tailoring run.

## The onboarding form round-trips

A returning candidate sees their previous answers pre-filled and edits them,
rather than retyping everything. `GET /api/onboard?include=payload` returns the
newest submission under the signed-in candidate's prefix — never anyone else's —
and the hosted form populates itself; a banner says what they're editing, with a
"Start over instead" escape.

**The blob is the record of what the candidate said; everything local is
derived from it.** `--fetch` takes the newest submission and overwrites
`config/profile.json` and `profile/*.md` — so don't hand-edit those expecting
edits to survive. If something in the profile is wrong, fix it in the form and
re-fetch. The resume is normalized on the way in, but the blob keeps the
candidate's original paste, which is what they see when they return.

Each edit writes a new blob, so submissions accumulate. `--fetch` always takes
the newest, so this is only cosmetic noise in `--check`; clear old ones
periodically.

## On-demand cover letters

The Desk has a **"Write this one ✍"** button on any job without a letter. It
does not generate anything client-side: `site/api/letter` queues one blob under
the candidate's `c/<id>/letter-requests/`, and `apply letter-worker` on this
machine (whose `.env` names that candidate) runs the real
`letters.py` — honesty validators intact — then exports and publishes. The app
polls `api/jobs` until `letterReal` flips, ~20–40s.

- Run the worker while reviewing: `apply letter-worker --watch`. Nothing happens
  without it; after ~2 minutes the button says so.
- **The worker is adaptive, and it stops on its own.** Every pass costs one
  metered Vercel Blob `list()` whether or not there is work, so a flat 20s loop
  is ~4,300 calls a day finding an empty queue — which is what suspended the
  store on 27 August 2026. It polls every 20s while work is arriving and for
  three minutes after, backs off to 90s when idle, and **exits after 5 idle
  minutes**. The idle clock starts when the worker does, so starting it and then
  not clicking anything for five minutes will end it; restarting is cheap, and
  `--max-idle 30` or `--max-idle 0` covers a long review session.
  `tests/test_letter_worker_idle.py` asserts the COST rather than the result,
  because the letters served are identical whether it polls 15 times or 4,300.
- Deliberately **not** a Vercel function calling Anthropic: that would fork the
  validators into JavaScript and put the candidate's resume, voice file, and an
  Anthropic key on Vercel. Today none of those leave this machine.
- An explicit click generates a letter for **any** job, including weak-tier —
  a human asking outranks the rubric.

## Known issues / traps

- **`site/api/onboard.js` cleans submissions against an ALLOWLIST.** A field
  added to the form in `onboard.py` but not added to `TEXT_FIELDS`/`BOOL_FIELDS`
  is **silently dropped in transit** — the form saves, the API returns 200, and
  the answer never reaches the blob. No error anywhere. This bit `jsearch_queries`
  on 5 August 2026. A form field spans three places: the input, the allowlist,
  and `save_all()`. `tests/test_payload_shape.py` now checks all three agree —
  **run it after touching the form**:
  ```bash
  .venv/bin/python3 tests/test_payload_shape.py
  ```
  Diagnostic tell: inspect the stored payload. A key that is *absent* means the
  server dropped it; a key that is *empty* means the candidate left it blank.
- **The local form writes straight into `config/` and `profile/`.** Testing
  `POST /save` against a running `apply onboard` creates a real profile. It backs
  up anything it replaces to `*.bak`, but clean up test runs or a junk candidate
  gets picked up by the next `match`.
- **`site/login.html` still said "Jordan's job queue"** — a leftover persona from
  the template that a real candidate would have seen. Now generic. `Jordan` also
  appears in `inbox_worker.py` comments.
- **Don't put the candidate's name anywhere in this repo.** It is public. Real
  candidate data is gitignored (`config/profile.json`, `profile/*.md`) — keep it
  that way.
- **A submission holds real personal data** (resume, contact, comp floor)
  in Mark's Vercel Blob. Private store, Google sign-in, per-candidate prefix —
  but it is their data on Mark's infrastructure, and they should know that.
- **The template persona is gone — keep it that way.** `letters.py`,
  `export_desk.py`, `resume_doc.py`, and `tailor.py` all hardcoded a fictional
  candidate ("Jordan Rivers"): the letter signature, the resume title, the voice
  anchors, and a tailoring instruction claiming the candidate wanted
  administrative work. Identity now comes from `config/profile.json` via
  `util.candidate_name()`, and voice from `profile/voice_real.md` via
  `util.candidate_voice()`. If a new prompt needs the candidate's name or
  register, read it from the profile — never inline it.
- **`voice_real.md` was dead for the whole project's life** — written by
  onboarding, described in docstrings, loaded by nothing. It now feeds the
  cover-letter style anchors, with a fallback instruction (not someone else's
  letters) when it's empty. Facts still come only from `resume.md` +
  `experience_bank.md`; writing samples shape register, never claims.
- **`profile.json.candidate.name` is what gets signed.** Mark's test submission
  said "Mark", so letters signed "Mark". Use a full name.
- **Single-writer SQLite.** One host owns `jobs.db`. Never put it on a sync
  drive; two writers through a sync layer corrupt it silently.

## The multi-candidate Desk — live since 12 September 2026

Deployed and proven the same day: Mark signed in with his operator Gmail,
picked the candidate slot, and saw client 1's queue. The actual addresses
live in each checkout's untracked `CLIENT.md` — this repo is public, and a
candidate's or operator's address does not belong in it. What is in place:

- **Google Cloud:** OAuth client "The Desk" in the Solis project, client id
  `982261074519-s9ogtdfsrd0gafehtjep0uv1pc4l0noo.apps.googleusercontent.com`,
  redirect URI `https://job-desk-theta.vercel.app/api/auth/callback/google`.
  The consent screen's test-user list (if still in Testing mode) must carry
  every address on the roster.
- **Vercel env on `job-desk`:** `DESK_GOOGLE_CLIENT_ID`, `DESK_OPERATORS`
  (Mark's Gmail), `DESK_CANDIDATES` (client 1's slot, see `CLIENT.md`) as
  plain variables so they can be read back and compared;
  `DESK_GOOGLE_CLIENT_SECRET` and `DESK_SESSION_SECRET` Sensitive.
  `DESK_PASSWORD` removed. **A variable change needs a redeploy** to reach the
  functions — `cd site && ./deploy.sh`.
- **Blobs:** copied 12 Sep under `c/04c243ca9efab57f/` (the stand-in address),
  8 blobs, verified identical. The flat copies at the store root are the
  rollback; nothing reads them.
- **The candidate slot is a stand-in.** Client 1's Google address was unknown
  on 12 Sep, so one of Mark's own addresses holds the slot (named in
  `CLIENT.md`). When the real address is known: add it to `DESK_CANDIDATES`,
  redeploy, run `bin/migrate_blobs.py --email <theirs> --apply`, and change
  `CANDIDATE_EMAIL` in this checkout's `.env`.

Two traps met on the way, both in this file's family: the first client secret
Mark stored was rejected by Google (`invalid_client`) — the callback's log line
named it in one read, because the token endpoint's body is logged verbatim —
and the Vercel project stores NEW variables as Sensitive by default, so the
three plain ones had to be re-added with `--no-sensitive` before their values
could be verified by pull-and-compare.

Known gaps: `review-app/guide.html` is not shipped (it embeds one candidate's
queue); `bin/build_guide.py` needs a sample-data mode before the Help tour
returns. The `apply-assistant-2` Vercel project and the empty blob store that
had been attached to it were both deleted 12 Sep 2026.
`load_tailored()` still keys by job id alone — safe while each checkout keeps
its own database, which is the rule.

## The search fixes — 12 September 2026

A read of the real funnel (7,861 jobs, 7,823 knocked out, zero strong matches
ever, top score 72 against a 75 threshold) showed the engine was not failing to
RANK good jobs, it was failing to let them through. What changed, and why:

- **Every knockout comparison is accent-folded and word-bounded**
  (`knockout.fold()`, `keyword_hit()`, `title_on_target()`). "san jose" now
  matches "San José"; 45 postings had died on that accent, three of them
  on-target QA roles in the candidate's home city.
- **A bare country is not a location mismatch.** "United States", "USA",
  "Remote - United States" pass (`is_bare_us()`); 138 rows had been rejected.
- **Target roles widen to their field stem** (`role_targets()`): a target
  ending in a role noun with two or more words before it — "quality assurance
  specialist" — also matches "quality assurance", because the noun is what the
  seniority rules judge. One-word remainders never widen ("compliance analyst"
  stays exact). Replaying the rules over the live DB: 38 survivors became 64,
  none lost; some of the 26 are semiconductor QA the scorer will grade weak,
  which is the division of labour — the filter admits the field, the model
  judges the fit.
- **Enrichment scrapes only rows the filter has PASSED** (`knockout = 0`, not
  COALESCE), gives up on a URL after three failures (`ENRICH_MAX_FAILURES`),
  and `bin/scheduled.sh` now runs match → enrich → match instead of enrich
  → match. 51 of 57 enriched rows had been knocked-out jobs.
- **Scoring is keyed on a content hash** (`score.content_hash`, stored in
  `scored_hash`): a job is re-scored when its text changed, with no flag. The
  description cap went from 1,400 to 6,000 characters (`APPLY_SCORE_DESC_CAP`);
  123 of 135 scored jobs had been truncated.
- **`apply prune`** archives rows not seen for 35 days before the NEWEST sweep
  (a flag, `archived=1`, reversible; manual adds and decided rows are kept).
  7,352 stale tech-profile rows were archived on 12 Sep; the pre-change DB is
  at `data/backups/jobs-2026-09-12-pre-phase2.db`.
- **`apply decisions`** pulls the Desk's `status.json` onto job rows (`status`,
  `decided_at`) and prints tier vs decision — the feedback loop that had never
  been closed.
- **Desk dedupe** collapses same-employer postings whose title token sets are
  nested or ≥0.6 Jaccard, or that share an apply URL (`export_desk.same_posting`).

Tests: `tests/test_search_fixes.py`, each case named for the row or count it
fixes. Not done, deliberately: a commute-radius rule (needs geodata), and more
JSearch queries (the candidate owns that list; the quota has headroom).

## Open threads (21 August 2026)

**The candidate's submission is the source of truth — never hand-edit the derived
files.** `config/profile.json` and `config/sources.json` are regenerated by
`apply onboard --fetch`; editing them is an override that the next fetch
silently discards. To change salary, locations, titles or skills, open
`/onboard` (it pre-fills with the last submission), change the fields, resubmit,
then re-fetch.

**Resolved: salary floor, locations and seniority all come from the form now.**
The candidate iterated hard — 16 submissions across 4–6 August — and the current
submission is authoritative for all three. **A figure relayed verbally may be
out of date**: an earlier spoken salary floor was superseded by a lower one the
candidate set himself, and confirmed as deliberate. Read the values from
`config/profile.json`; never "correct" them from memory or hearsay. Older
submissions were cleared on 21 August, leaving one.

**Search phrases are a form field now, not a derived value.** Generating
`jsearch_queries` from titles + skills + locations was wrong in a specific way:
the generator leads with the candidate's *home town* and keeps only the first
few locations, so seven of ten queries named a small town with little of the
candidate's industry in it, while the towns where that industry actually
concentrates — listed later in their locations — were cut entirely. Skills
couldn't fix it, because no skills answer can express "this town matters more
than the one I live in." Step 2 of the form now has an optional **Search
phrases** box, one per line, used verbatim; the generator survives only as the
blank-field fallback. Tuning therefore lives in the submission and survives a
re-fetch, which was the point of the original change.

- Parsed with `_split_lines()`, not `_split()` — a phrase reads
  `title in City, ST` and comma-splitting tears it in half.
- Submissions predating the field simply have no key and fall back cleanly.
- **The generator still leads with the home town.** It's only reached when the
  box is blank, but if a second candidate ever onboards, that ordering is the
  thing to revisit.

**Resolved: seniority ceiling is `director`.** On-domain director roles that
were previously knocked out now reach the shortlist.

**Live hazard — `load_tailored()` ignores the content hash.** It fetches a
tailored resume by job ID alone (`tailor.py`), so if a second candidate is ever
run through the same database, one can be served the other's tailored resume.
Verified clean today only because the cache was regenerated against the current
resume. Guard it before onboarding anyone else.

**Stale notes are the recurring failure here.** Twice in one session, statements
carried forward from earlier in a conversation ("one commit unpushed", "the
floor is an override") were wrong because other sessions had moved on. Check the
repo and the blob before repeating anything from memory.

**Sourcing reality as of 21 Aug:** 31 survive knockout — 18 stretch, 13 weak; 26 exported, all 18 stretch carrying a tailored resume, 8 with letters. Of the six
careers pages in the current source list, three scrape fine and three return
nothing — they are JavaScript portals that render no markup to a scraper, so
expect roughly half of any hand-listed employer set to be unreachable. JSearch
works via `/search-v2`. The most productive single query is the one naming the
industry's regional hub rather than the candidate's home town. Re-sweep weekly;
boards turn over completely.

**Knocked-out rows keep stale scores.** Scoring only touches survivors, so
filtered jobs retain whatever tier they last had — including from a previous
candidate. Always filter `knockout=0 AND COALESCE(archived,0)=0` when querying
the DB by hand.

## Considered and deferred

**Provenance highlighting in the cover letter** — considered 3 August 2026, deferred.

The tailored resume highlights changed lines because tailoring is a constrained
permutation: each output bullet carries a `source` index into the base resume,
so `changed` is computable. **A cover letter has no "before" to diff against**,
so the same mechanism cannot apply — this is by design, not a missing feature.
The letter's equivalent guarantee is the validator (every number traces to the
fact sources, employer named, banned phrases rejected, fails closed).

The idea worth keeping: not diff highlighting but **per-claim provenance** —
tap a sentence, see which resume bullet supports it. Stronger than today's
check, which confirms each number appears *somewhere* in the facts rather than
that a specific claim traces to a specific line. It would need `letters.py` to
return paragraph→bullet mappings alongside the text, the validator to verify
each mapping actually supports its claim, and a tap affordance in the Desk. The
`trace()` plumbing and `mapsTo` concept already exist from the resume diff.

Deferred because the letters are already fact-validated and a human reads every
one before sending, so the marginal safety is small next to getting the first
candidate onboarded. Revisit if letter volume makes spot-checking tedious.

## How to work here

- Mark is technical (three decades on the architectural side at Adobe). Don't
  explain programming basics.
- Verify before calling anything done — run it, show the output.
