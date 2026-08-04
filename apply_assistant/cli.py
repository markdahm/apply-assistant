from __future__ import annotations

import argparse
import json
import time

from . import db as dbm
from .classify import LANE_LABELS
from .match import run_match
from .paths import DEFAULT_DB
from .sweep import load_config, run_sweep


def cmd_sweep(args):
    print("Running sourcing sweep...\n")
    config = load_config(args.config)
    report = run_sweep(config=config, db_path=args.db)
    ok = [s for s in report["sources"] if s["ok"]]
    bad = [s for s in report["sources"] if not s["ok"]]
    print("\n-- sweep complete --")
    print("  fetched: {0}   new: {1}   updated: {2}   in-run dupes skipped: {3}".format(
        report["fetched"], report["inserted"], report["updated"], report["skipped_dupes"]))
    print("  total in DB: {0}   (unique postings: {1})".format(
        report["total_in_db"], report["unique_dedupe_keys"]))
    print("  sources ok: {0}   failed (bad slug / unreachable): {1}".format(len(ok), len(bad)))
    for s in bad:
        print("    -- {0}: {1}".format(s["source"], s.get("error", "")))


def cmd_stats(args):
    conn = dbm.connect(args.db)
    c = dbm.counts(conn)
    print("Total jobs: {0}   (new: {1})\n".format(c["total"], c["new"]))
    print("By lane (how we'd apply):")
    for lane, n in c["by_lane"].items():
        print("  {0}  {1:>5}   {2}".format(lane, n, LANE_LABELS.get(lane, "")))
    print("\nBy source:")
    for s, n in c["by_source"].items():
        print("  {0:<16} {1:>5}".format(s, n))
    print("\nTop companies:")
    for co, n in c["by_company"].items():
        print("  {0:<34} {1:>4}".format((co or "")[:34], n))
    conn.close()


def _comp_str(r):
    if r["comp_min"] or r["comp_max"]:
        lo = int(r["comp_min"] or 0) // 1000
        hi = int(r["comp_max"] or 0) // 1000
        cur = r["comp_currency"] or "$"
        return "  {0}{1}k-{2}k".format(cur, lo, hi)
    if r["comp_text"]:
        return "  " + r["comp_text"]
    return ""


def cmd_list(args):
    conn = dbm.connect(args.db)
    rows = dbm.query_jobs(
        conn, lane=args.lane, source=args.source, company=args.company,
        status=("new" if args.new else None), limit=args.limit,
    )
    for r in rows:
        print("[{0}] {1}  --  {2}  ({3}){4}".format(
            r["lane"], r["title"], r["company"], r["location"] or "n/a", _comp_str(r)))
        print("     {0} · {1} · {2}".format(r["uid"], r["source"], r["apply_url"]))
    print("\n{0} jobs".format(len(rows)))
    conn.close()


def cmd_match(args):
    print("Running knockout + match scoring...\n")
    rep = run_match(
        db_path=args.db, profile_path=args.profile, limit=args.limit,
        rescore=args.rescore, method=("heuristic" if args.heuristic else "auto"),
    )
    print("\n-- match complete --")
    print("  profile: {0}".format(rep["profile"]))
    print("  total: {0}   knocked out: {1}   survivors: {2}".format(
        rep["total"], rep["knocked_out"], rep["survivors"]))
    print("  scored this run: {0}".format(rep["scored"]))
    for t in ("strong", "stretch", "weak"):
        if t in rep["by_tier"]:
            print("    {0:<8} {1}".format(t, rep["by_tier"][t]))


def cmd_shortlist(args):
    conn = dbm.connect(args.db)
    rows = dbm.shortlist(conn, tier=args.tier, limit=args.limit)
    for r in rows:
        gaps = ""
        try:
            g = json.loads(r["match_gaps"] or "[]")
            if g:
                gaps = "   gaps: " + ", ".join(g[:3])
        except (ValueError, TypeError):
            pass
        print("[{0:<7} {1:>3}] {2}  --  {3}  ({4})".format(
            (r["match_tier"] or "?").upper(), int(r["match_score"] or 0),
            r["title"], r["company"], r["location"] or "n/a"))
        print("     {0}{1}".format(r["match_why"] or "", gaps))
        print("     {0} · lane {1} · {2}".format(r["uid"], r["lane"], r["apply_url"]))
    print("\n{0} jobs".format(len(rows)))
    conn.close()


def cmd_export(args):
    from .export_desk import DESK_JS, write_desk_js
    floor = None
    try:
        from .match import load_profile
        prof, _ = load_profile()
        floor = prof.get("preferences", {}).get("comp_floor")
    except Exception:  # noqa: BLE001 - profile is optional for export
        pass
    n, resume_report = write_desk_js(db_path=args.db, comp_floor=floor)
    print("Exported {0} strong/stretch jobs -> {1}".format(n, DESK_JS))
    print("Resume artifacts: general pdf={0}, per-job pdfs={1} (failures: {2})".format(
        resume_report.get("pdf"), resume_report.get("job_pdfs", 0),
        resume_report.get("job_pdf_failures", 0)))
    if not resume_report.get("pdf"):
        print("  !! PDF generation failed: {0}".format(resume_report.get("pdf_error", "unknown")))
    print("Open review-app/\"The Desk - Triage.dc.html\" (or serve the folder) to review them.")


def cmd_enrich(args):
    from .enrich import run_enrich
    print("Enriching job detail pages via Firecrawl...\n")
    rep = run_enrich(db_path=args.db, limit=args.limit)
    print("\n-- enrich complete --")
    print("  attempted: {0}   enriched: {1}   salaries found: {2}   benefits found: {3}   failed: {4}".format(
        rep["attempted"], rep["enriched"], rep["salary"], rep["benefits"], rep["failed"]))
    for e in rep.get("errors", [])[:5]:
        print("   !! " + e)
    if rep["enriched"]:
        print("Now re-run: match --rescore, tailor, export (descriptions changed).")


def cmd_add(args):
    from .manual import run_add

    report = run_add(args.urls, with_pipeline=not args.no_pipeline)
    print("\n-- add complete --")
    print("  new: {0}   updated: {1}   failed: {2}".format(
        len(report["added"]), len(report["updated"]), len(report["failed"])))
    for url, why in report["failed"]:
        print("   !! {0}: {1}".format(url[:60], why))
    if report["added"] or report["updated"]:
        print("  now run: export (or let the inbox worker publish)")


def cmd_letters(args):
    from .letters import LETTER_MODEL, run_letters
    print("Writing cover letters in Jordan's voice via {0}...\n".format(LETTER_MODEL))
    rep = run_letters(db_path=args.db, limit=args.limit, force=args.force)
    print("\n-- letters complete --")
    print("  written: {0}   failed: {1}   already cached: {2}".format(
        rep["written"], rep["failed"], rep["cached"]))
    for e in rep.get("errors", [])[:6]:
        print("   !! " + e)


def cmd_tailor(args):
    from .tailor import TAILOR_MODEL, run_tailor
    print("Tailoring resumes for the shortlist via {0}...\n".format(TAILOR_MODEL))
    rep = run_tailor(db_path=args.db, limit=args.limit, force=args.force)
    print("\n-- tailor complete --")
    print("  tailored: {0}   failed: {1}   already cached: {2}".format(
        rep["tailored"], rep["failed"], rep["cached"]))
    for e in rep.get("errors", [])[:6]:
        print("   !! " + e)
    if rep["tailored"] or rep["failed"] == 0:
        print("Run `export` to render the tailored previews + per-job PDFs.")


def cmd_onboard(args):
    from . import onboard as ob

    if args.emit_html:
        print("wrote {0}".format(ob.emit_html(args.emit_html)))
        return

    if args.check:
        try:
            subs = ob.read_submissions()
        except RuntimeError as e:
            print("Can't read the queue: {0}".format(e))
            return
        if not subs:
            print("No remote submissions yet.")
            return
        print("{0} submission(s) waiting:".format(len(subs)))
        for i, e in enumerate(subs):
            when = time.strftime("%Y-%m-%d %H:%M",
                                 time.localtime((e.get("submittedAt") or 0) / 1000))
            print("  [{0}] {1:20} {2}".format(
                i, (e.get("payload", {}).get("name") or "(unnamed)")[:20], when))
        print("\nPull the newest with `apply onboard --fetch`.")
        return

    if args.fetch:
        try:
            report, entry = ob.fetch_and_save(index=args.pick)
        except (RuntimeError, IndexError) as e:
            print("Nothing pulled: {0}".format(e))
            return
        name = entry.get("payload", {}).get("name") or "(unnamed)"
        print("Pulled submission from: {0}".format(name))
        for p in report["wrote"]:
            print("    wrote   {0}".format(p))
        for b in report["backed_up"]:
            print("    backup  {0}".format(b))
        if report.get("sources"):
            print("  {0} target employer(s) routed into config/sources.json".format(
                report["employers"]))
        print("\nNext: `apply sweep` then `apply match`.")
        return

    ob.run_onboard(port=args.port, open_browser=not args.no_open)


def cmd_show(args):
    conn = dbm.connect(args.db)
    r = dbm.get_job(conn, args.uid)
    if not r:
        print("not found")
        return
    for k in r.keys():
        if k in ("raw_json", "description"):
            continue
        print("{0:14}: {1}".format(k, r[k]))
    print("\n--- description ---")
    print((r["description"] or "")[:2500])
    conn.close()


def main(argv=None):
    p = argparse.ArgumentParser(prog="apply-assistant", description="Job sourcing engine")
    p.add_argument("--db", default=str(DEFAULT_DB), help="SQLite DB path")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("onboard", help="set up a candidate (local form, or pull a remote one)")
    s.add_argument("--port", type=int, default=8765, help="port for the local onboarding app")
    s.add_argument("--no-open", action="store_true", help="don't auto-open the browser")
    s.add_argument("--check", action="store_true",
                   help="list remote submissions waiting in the queue")
    s.add_argument("--fetch", action="store_true",
                   help="pull the newest remote submission and write the profile files")
    s.add_argument("--pick", type=int, default=None,
                   help="with --fetch: index from --check instead of the newest")
    s.add_argument("--emit-html", metavar="PATH", default=None,
                   help="write the standalone hosted form (used by site/deploy.sh)")
    s.set_defaults(func=cmd_onboard)

    s = sub.add_parser("sweep", help="pull all sources into the DB")
    s.add_argument("--config", default=None, help="path to sources.json")
    s.set_defaults(func=cmd_sweep)

    s = sub.add_parser("stats", help="summary counts by lane/source/company")
    s.set_defaults(func=cmd_stats)

    s = sub.add_parser("list", help="list jobs")
    s.add_argument("--lane", help="filter by lane A/B/C/D")
    s.add_argument("--source", help="filter by source")
    s.add_argument("--company", help="filter by company substring")
    s.add_argument("--new", action="store_true", help="only status=new")
    s.add_argument("--limit", type=int, default=40)
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("show", help="show one job by uid (prefix ok)")
    s.add_argument("uid")
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("match", help="knockout filter + LLM rubric scoring")
    s.add_argument("--profile", default=None, help="path to profile.json")
    s.add_argument("--limit", type=int, default=None, help="cap jobs scored this run")
    s.add_argument("--rescore", action="store_true", help="re-score already-scored jobs")
    s.add_argument("--heuristic", action="store_true", help="force the no-API keyword scorer")
    s.set_defaults(func=cmd_match)

    s = sub.add_parser("shortlist", help="top matches the human should review")
    s.add_argument("--tier", help="filter by tier strong/stretch/weak")
    s.add_argument("--limit", type=int, default=30)
    s.set_defaults(func=cmd_shortlist)

    s = sub.add_parser("export", help="export the shortlist into the review app (The Desk)")
    s.set_defaults(func=cmd_export)

    s = sub.add_parser("tailor", help="tailor the resume to each shortlisted job (LLM, cached)")
    s.add_argument("--limit", type=int, default=None, help="cap jobs tailored this run")
    s.add_argument("--force", action="store_true", help="regenerate even if cached")
    s.set_defaults(func=cmd_tailor)

    s = sub.add_parser("enrich", help="scrape each surviving job's detail page for description/salary/benefits")
    s.add_argument("--limit", type=int, default=40, help="max detail pages to scrape (credits)")
    s.set_defaults(func=cmd_enrich)

    s = sub.add_parser("letters", help="write a cover letter for each shortlisted job (LLM, cached)")
    s.add_argument("--limit", type=int, default=None, help="cap letters this run")
    s.add_argument("--force", action="store_true", help="regenerate even if cached")
    s.set_defaults(func=cmd_letters)

    s = sub.add_parser("add", help="manually add job URLs: scrape, score, tailor, letter")
    s.add_argument("urls", nargs="+", help="one or more job posting URLs")
    s.add_argument("--no-pipeline", action="store_true", help="ingest only; skip score/tailor/letter")
    s.set_defaults(func=cmd_add)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
