# apply-assistant

A quality-over-spam job-search engine for one real person. **The AI does the
grind — find the jobs, filter the junk, tailor a starting point — and the human
keeps the voice and the final click.** No spray-and-pray, no automating anyone's
logged-in account, and nothing invented: every line of a tailored resume traces
to a real bullet, and every claim in a cover letter is validated against the
candidate's own facts.

The pipeline runs end to end:

```
 sweep ──▶ enrich ──▶ match ──▶ tailor ──▶ letters ──▶ export ──▶ publish
  │          │          │          │          │           │          │
 pull     scrape     knockout   rewrite    draft in    render     push to
 every    detail     + LLM      the resume  the        "The       the review
 source   pages      rubric     per job     candidate's Desk"      app (live)
                     scoring    (honest)    voice
```

Everything before the final click is automated; the click stays with the human.

---

## What's interesting here (for a reviewer)

- **Honesty is enforced in code, not vibes.** The resume tailor is a constrained
  transform — fixed section headers, bullets are a strict 1:1 permutation of the
  source bullets (by index), no new numbers per role, skills must be a subset of
  the real skill list. The cover-letter writer validates that every number in the
  letter appears in the fact sources, bans a list of AI-tell phrases, requires the
  employer be named, and gates faith/personal language. Both **fail closed** to an
  honest placeholder rather than shipping something unverifiable.
  → `apply_assistant/tailor.py`, `apply_assistant/letters.py`

- **Apply-method routing (Lanes A–D).** Every job is tagged with *how* it would be
  applied to, so the review layer knows what each application actually needs:

  | Lane | Meaning |
  |------|---------|
  | **A** | email — auto-send after the human approves (their own mailbox) |
  | **B** | clean ATS form (Greenhouse / Lever / Ashby / …) — pre-fill + one-click handoff |
  | **C** | hard portal (Workday / iCIMS / NeoGov) — best-effort, manual finish |
  | **D** | do-not-automate (LinkedIn / Indeed) — surface only, apply natively |

- **Two-stage matching so the human only ever sees good fits.** A free,
  deterministic *knockout* filter drops the un-gettable/unwanted (wrong role,
  seniority, location, comp floor, dealbreakers) — mirroring the only thing that
  truly auto-rejects in real ATSs — then an LLM *rubric* scores each survivor
  0–100 into `strong` / `stretch` / `weak` with the gaps and a one-line "why this
  fits *you*". Only strong/stretch reach a person.

- **Cost-aware model tiering.** High-volume scoring runs on a cheap model
  (Haiku); the judgment-heavy tailoring and letter-writing run on a flagship
  model (Opus). All LLM output is cached in SQLite by a content hash, so re-runs
  are free unless an input actually changed.

- **The sources that don't have APIs.** Public ATS feeds (Greenhouse, Lever,
  Ashby, Workable, SmartRecruiters, Workday CXS) are read straight from their
  JSON — free, no auth. Everything else (NeoGov/governmentjobs, iCIMS, PageUp,
  museum/nonprofit sites) is read via **Firecrawl**, which is used **read-only —
  it never submits an application**. LinkedIn/Indeed can't be scraped directly
  (blocked, ToS), so those ride **JSearch** (Google for Jobs) instead.

---

## Quickstart

```bash
git clone <this-repo> && cd apply-assistant
python -m venv .venv && source .venv/bin/activate
pip install -e .            # or: pip install -r requirements.txt

cp .env.example .env        # fill in the keys you have (all optional)
```

Set up the candidate the easy way — a browser form:

```bash
apply onboard                     # opens a local web app; fill it in, hit Finish
```

`onboard` launches a small self-contained web app (Python's built-in server, no
extra deps) that walks the person through four steps and writes
`config/profile.json`, `config/sources.json`, and the `profile/*.md` files for
them — location defaults are pre-set for a South Bay job search. It never
clobbers existing files (it backs them up to `*.bak` first). Prefer to do it by
hand? Create `config/profile.json` from `config/profile.example.json` and the
files in [`profile/`](profile/README.md) yourself.

**When the candidate isn't at your keyboard.** The local form only listens on
localhost, so for someone remote the same form is deployed with the review app
at `/onboard`, behind the Desk's password. They fill it in on their own time;
their answers park in Vercel Blob; you pull them down when they're ready:

```bash
apply onboard --check             # who's submitted, and when
apply onboard --fetch             # newest submission -> the same local files
```

`--fetch` runs the identical `save_all()` the local form uses, so both routes
produce byte-identical output (and the same `*.bak` protection). The hosted page
is *generated* from the same template at deploy time — `site/deploy.sh` bakes it
via `apply onboard --emit-html` — so the two forms cannot drift apart. Pulling
needs `BLOB_READ_WRITE_TOKEN`; it reads Blob directly, so no Vercel CLI on the
pipeline host. Then:

```bash
apply sweep                       # pull every source into the DB
apply stats                       # counts by lane / source / company
apply match --limit 120           # knockout everything, LLM-score N survivors
apply shortlist --tier strong     # what a human should review
apply enrich --limit 40           # scrape detail pages (description/salary/benefits)
apply tailor                      # per-job resume, honesty-validated + cached
apply letters                     # per-job cover letter in the candidate's voice
apply export                      # render into the review app
apply add <url> [<url> ...]       # hand-add a posting: scrape → score → tailor → letter
```

`apply` is the console entry point (installed by `pip install -e .`); every
command is also reachable as `python -m apply_assistant.cli <command>`.

> **macOS:** activate the virtualenv before using the bare `apply` command.
> macOS ships its own `/usr/bin/apply`, which otherwise shadows this one and
> fails with `command not found: <first argument>`. `.venv/bin/apply <command>`
> works without activating.

### Keys & degradation

Every API key is optional and the engine degrades gracefully:

| Key | Unlocks | Without it |
|-----|---------|------------|
| `ANTHROPIC_API_KEY`   | LLM scoring, resume tailoring, cover letters | heuristic scorer; no tailoring/letters |
| `FIRECRAWL_API_KEY`   | scraping boards with no API + detail enrichment | API-only sources (Greenhouse/Lever/…/Workday) |
| `JSEARCH_API_KEY`     | LinkedIn/Indeed via Google for Jobs | those aggregators are surfaced only via manual `add` |
| `BLOB_READ_WRITE_TOKEN` | live publish + manual-link queue for the web app | local review only |

---

## The Desk — review app

[`review-app/`](review-app/) is a single-file review UI (a template-driven
component with a self-booting React runtime). The reviewer sees each job's fit
rating and the reasons, reads/edits the drafted cover letter and the tailored
resume (rendered to look like the real PDF, with the changed lines highlighted),
and sets a status — **Interested / Applied / Interviewing / Ignored / Not
interested** — or opens the employer portal.

[`site/`](site/) deploys it (Vercel) behind a password, with a mobile companion
at `/m` and serverless functions for:

- `api/status` — persist the reviewer's decisions/edits (Vercel Blob)
- `api/pdf` — render edited resume/letter HTML to a one-page PDF (headless Chromium)
- `api/inbox` — queue manually-added job links (one blob per link, no lost updates)
- `api/jobs` — serve the freshest published dataset so new jobs appear without a redeploy
- `api/onboard` — accept a remote candidate's onboarding answers (one blob per
  submission; the blob carries real personal data, so it gets an unguessable
  suffix and the page sits behind the password gate)

`apply export` writes the data the app reads; `apply publish` (or the daily job)
pushes it live.

---

## Automation

`bin/daily.sh` runs the whole pipeline; `bin/inbox.sh` processes the
manual-link queue. Both load a local `.env` and are meant to be scheduled
(cron / systemd / launchd) on a single always-on host — e.g. the daily pipeline
in the morning and the inbox worker every ~10 minutes. `BOARDS_EVERY_N_DAYS`
throttles the paid Firecrawl board scrapes to fit a credit budget.

---

## Design notes

- **Single-writer SQLite.** The job DB lives outside the repo
  (`~/.apply-assistant/jobs.db`, override with `APPLY_DB`) and must be written by
  only one host. SQLite through a file-sync layer (Dropbox/Syncthing/iCloud)
  silently corrupts — two hosts writing a synced DB once dropped a record's flag.
  One host owns the DB and is the single publish authority.
- **Firecrawl is read-only.** It never submits an application — that would run
  from datacenter IPs and trip ATS fraud filters. The submit click always stays
  with the human.
- **Exactly-once-ish publishing.** The manual-link queue stores one blob per
  link (no read-modify-write races); the pipeline upserts by URL so reprocessing
  a link is harmless.

## Project layout

```
apply_assistant/          # the engine (pip-installable package)
  sources/                #   one adapter per source (greenhouse, lever, workday, firecrawl, jsearch, …)
  sweep.py                #   pull every source → normalize → dedupe → SQLite
  classify.py knockout.py #   apply-method lanes; deterministic knockout filter
  score.py match.py       #   LLM rubric scoring
  enrich.py               #   Firecrawl detail-page scrape (description/salary/benefits)
  tailor.py letters.py    #   honesty-validated resume + cover-letter generation
  export_desk.py publish.py  # render + publish to the review app
  manual.py inbox_worker.py  # hand-added links
config/                   # profile + source lists (examples tracked; real profile gitignored)
profile/                  # candidate master profile (gitignored; see profile/README.md)
review-app/ site/         # the review UI + its Vercel deployment
bin/                      # daily + inbox runner scripts
```

## License

MIT — see [LICENSE](LICENSE).
