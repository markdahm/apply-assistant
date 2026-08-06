# apply-assistant

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

- **URL:** https://job-desk-theta.vercel.app (password-gated; `/onboard` is the
  candidate form). Project `job-desk` under scope `mark-dahms-projects`,
  `prj_xqB6zvQBUrD68ohGy6pm5HXutRcF`.
- **Deploys are CLI-only — do NOT connect Git.** `site/onboard.html`,
  `site/desk.html` and `site/desk-data.js` are all gitignored generated files, so
  a Git-based build would ship a site with no onboarding form and no Desk.
  `./deploy.sh` generates them and uploads from the local machine.
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

## Running the CLI on this Mac

**`apply` collides with macOS's own `/usr/bin/apply`.** Without the virtualenv
active, `apply sweep` runs the BSD `apply(1)` utility instead and fails with a
confusing `command not found: <first argument>`. Either activate the venv, or
call the entry point by path — which needs no activation:

```bash
/Users/markdahm/Desktop/OS/development/apply-assistant/.venv/bin/apply <command>
```

Every bare `apply …` line in the README assumes an active venv.

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
newest submission (password-gated, same as the rest of the site) and the hosted
form populates itself; a banner says what they're editing, with a "Start over
instead" escape.

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
`letter-requests/`, and `apply letter-worker` on this machine runs the real
`letters.py` — honesty validators intact — then exports and publishes. The app
polls `api/jobs` until `letterReal` flips, ~20–40s.

- Run the worker while reviewing: `apply letter-worker --watch` (polls every 20s).
  Nothing happens without it; after ~2 minutes the button says so.
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
  in Mark's Vercel Blob. Unguessable URL, password-gated page — but it is his
  data on Mark's infrastructure, and he should know that.
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

## Open threads (5 August 2026)

**The candidate's submission is the source of truth — never hand-edit the derived
files.** `config/profile.json` and `config/sources.json` are regenerated by
`apply onboard --fetch`; editing them is an override that the next fetch
silently discards. To change salary, locations, titles or skills, open
`/onboard` (it pre-fills with the last submission), change the fields, resubmit,
then re-fetch.

**Resolved 5 August: the revised salary floor and the added location are no
longer overrides.** They went through the form and were pulled down with
`--fetch`; the rewritten `profile.json` came back byte-identical to the
hand-edited one, which is the proof that the form now produces those values on
its own. `comp_floor` is 85000 and it will survive every future fetch.

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

**Unanswered: seniority ceiling.** Two on-domain roles were knocked out as
"too senior (director)". Worth asking whether the ceiling should rise — a
director title at a mid-size employer is not always a senior role.

**Sourcing reality as of 4 Aug:** 29 survive knockout, 1 stretch. Of the six
careers pages in the current source list, three scrape fine and three return
nothing — they are JavaScript portals that render no markup to a scraper, so
expect roughly half of any hand-listed employer set to be unreachable. JSearch
works via `/search-v2`. The most productive single query is the one naming the
industry's regional hub rather than the candidate's home town. Re-sweep weekly;
boards turn over completely.

**Knocked-out rows keep stale scores.** `match --rescore` only rescores
survivors, so filtered jobs retain whatever tier they last had — including from
a previous candidate. Always filter `knockout=0` when querying the DB by hand.

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
