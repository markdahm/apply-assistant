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

## Where things stand (3 August 2026)

- **Engine: complete and pushed.** All commands work.
- **Sources: verified live.** A full sweep pulled **7,289 jobs from 36 feeds,
  zero failures**, using only the free Greenhouse/Lever/Ashby JSON — no API keys.
  DB at `~/.apply-assistant/jobs.db`.
- **Remote onboarding: deployed and proven end to end.** A real browser
  submission went form → `api/onboard` → private blob → `apply onboard --check`
  → `fetch_and_save()`, with every field intact. The candidate fills the form at
  `/onboard`; Mark pulls it down with `apply onboard --fetch`.
- **The candidate has not submitted yet.** No `config/profile.json`, no `profile/*.md`.
  Until he does, `match`/`tailor`/`letters` have no candidate to work with.
- **No `.env` yet** — no API keys are set on this machine.

## Blockers before the candidate can start

1. **`vercel login`** — the CLI is installed but its token is expired, so
   `site/deploy.sh` can't deploy. Mark has to do this himself (browser auth).
2. **`DESK_PASSWORD` and `BLOB_READ_WRITE_TOKEN`** must exist on the `job-desk`
   Vercel project, and the Blob token must also be in a local `.env` for
   `apply onboard --fetch` to read the queue.

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

## How to work here

- Mark is technical (three decades on the architectural side at Adobe). Don't
  explain programming basics.
- Verify before calling anything done — run it, show the output.
