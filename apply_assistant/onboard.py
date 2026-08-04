"""The onboarding app: a friendly local web form that sets a real person up.

The engine needs three things before it can find anyone a job: a
``config/profile.json`` (identity + preferences that drive the knockout filter
and scoring), the ``profile/*.md`` master files (facts + voice the tailor and
letter-writer read), and a ``config/sources.json`` (where to look). Writing
those by hand is fiddly and easy to get wrong.

``apply onboard`` launches a small self-contained web app on localhost. The
person fills in four steps in their browser, hits submit, and every file lands
in the right place — ready for ``apply sweep``. No new dependencies: this is
Python's built-in HTTP server serving one embedded HTML page.

Defaults are pre-filled for a South Bay (San Francisco Bay Area) job search, so
most of onboarding is confirming rather than typing.
"""

from __future__ import annotations

import json
import re
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .paths import PROJECT_ROOT

CONFIG_DIR = PROJECT_ROOT / "config"
PROFILE_DIR = PROJECT_ROOT / "profile"

# South Bay default locations — the knockout filter matches these as substrings
# against each posting's location, so keep them lowercase and city-plain.
SOUTH_BAY_LOCATIONS = [
    "remote", "san jose", "sunnyvale", "santa clara", "mountain view",
    "palo alto", "cupertino", "milpitas", "campbell", "los gatos",
    "saratoga", "morgan hill", "gilroy", "fremont", "menlo park",
    "redwood city", "san francisco", "bay area", "south bay", "silicon valley",
]

# Sensible starting dealbreakers and off-target roles — the person edits these.
DEFAULT_EXCLUDE_ROLE = [
    "intern", "recruiter", "sales", "account executive",
]
DEFAULT_EXCLUDE_KW = [
    "security clearance", "active clearance", "ts/sci", "polygraph",
    "secret clearance",
]


def _split(text):
    """Comma- or newline-separated text -> a clean list of non-empty items."""
    if not text:
        return []
    parts = re.split(r"[,\n]", text)
    return [p.strip() for p in parts if p.strip()]


def _route_source(url):
    """Map a careers URL to (source_kind, value) so it feeds the right adapter.

    Public ATS feeds (Greenhouse/Lever/Ashby/Workable/SmartRecruiters/Workday)
    are read from JSON for free with no key. Anything else rides Firecrawl,
    which needs a key but works on any careers page. Returns (kind, value)
    where kind is one of the typed source arrays, or ("firecrawl_boards", url).
    """
    u = url.strip()
    low = u.lower()
    m = re.search(r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board\?for=)?([a-z0-9_-]+)", low)
    if m:
        return "greenhouse", m.group(1)
    m = re.search(r"jobs\.lever\.co/([a-z0-9_-]+)", low)
    if m:
        return "lever", m.group(1)
    m = re.search(r"jobs\.ashbyhq\.com/([a-z0-9_-]+)", low)
    if m:
        return "ashby", m.group(1)
    m = re.search(r"apply\.workable\.com/([a-z0-9_-]+)", low)
    if m:
        return "workable", m.group(1)
    m = re.search(r"([a-z0-9_-]+)\.workable\.com", low)
    if m and m.group(1) not in ("www", "apply"):
        return "workable", m.group(1)
    m = re.search(r"careers\.smartrecruiters\.com/([a-z0-9_-]+)", low)
    if m:
        return "smartrecruiters", m.group(1)
    m = re.search(r"([a-z0-9_-]+)\.(?:wd\d+)\.myworkdayjobs\.com/(?:[a-z-]+/)?([a-z0-9_-]+)", low)
    if m:
        return "workday", "{0}/{1}".format(m.group(1), m.group(2))
    return "firecrawl_boards", u


def build_profile(payload):
    """Turn the submitted form payload into the config/profile.json structure."""
    titles = _split(payload.get("titles"))
    # Seed the role keywords the knockout filter matches on from the job titles,
    # lowercased, unless the person supplied their own.
    target_kw = _split(payload.get("target_role_keywords")) or [t.lower() for t in titles]
    comp_floor = payload.get("comp_floor")
    try:
        comp_floor = int(comp_floor) if str(comp_floor).strip() else None
    except (TypeError, ValueError):
        comp_floor = None
    try:
        years = int(payload.get("years_experience") or 0)
    except (TypeError, ValueError):
        years = 0

    return {
        "candidate": {
            "name": payload.get("name", "").strip(),
            "summary": payload.get("summary", "").strip(),
            "titles": titles,
            "skills": _split(payload.get("skills")),
            "years_experience": years,
            "seniority": payload.get("seniority", "mid"),
            "work_authorization": payload.get("work_authorization", "").strip(),
        },
        "preferences": {
            "target_role_keywords": target_kw,
            "exclude_role_keywords": _split(payload.get("exclude_role_keywords")),
            "seniority_floor": payload.get("seniority_floor") or None,
            "seniority_ceiling": payload.get("seniority_ceiling") or None,
            "locations": _split(payload.get("locations")),
            "remote_ok": bool(payload.get("remote_ok")),
            "comp_floor": comp_floor,
            "exclude_keywords": _split(payload.get("exclude_keywords")),
            "needs_sponsorship": bool(payload.get("needs_sponsorship")),
        },
    }


def build_sources(employers):
    """Fresh sources.json from the person's target employers, correctly routed."""
    src = {
        "_note": "Target employers from onboarding. Public-ATS entries feed for "
                 "free; firecrawl_boards need a FIRECRAWL_API_KEY.",
        "greenhouse": [], "lever": [], "ashby": [], "workable": [],
        "smartrecruiters": [], "workday": [], "firecrawl_boards": [],
    }
    for e in employers:
        url = (e.get("url") or "").strip()
        name = (e.get("name") or "").strip()
        if not url:
            continue
        kind, value = _route_source(url)
        if kind == "firecrawl_boards":
            entry = {"url": value, "name": name or value}
            if entry not in src["firecrawl_boards"]:
                src["firecrawl_boards"].append(entry)
        elif value not in src[kind]:
            src[kind].append(value)
    return src


def _write_md(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "w") as f:
        f.write(content)


def _archive_existing(path):
    """Never clobber silently — move an existing file to a .bak alongside it."""
    if path.exists():
        bak = path.with_suffix(path.suffix + ".bak")
        path.replace(bak)
        return str(bak)
    return None


def save_all(payload):
    """Write every file the engine needs. Returns a report of what was written."""
    wrote, backed_up = [], []

    profile = build_profile(payload)
    ppath = CONFIG_DIR / "profile.json"
    bak = _archive_existing(ppath)
    if bak:
        backed_up.append(bak)
    with open(str(ppath), "w") as f:
        json.dump(profile, f, indent=2)
    wrote.append(str(ppath))

    employers = payload.get("employers") or []
    if employers:
        spath = CONFIG_DIR / "sources.json"
        bak = _archive_existing(spath)
        if bak:
            backed_up.append(bak)
        with open(str(spath), "w") as f:
            json.dump(build_sources(employers), f, indent=2)
        wrote.append(str(spath))

    # Master profile files — the facts/voice authority for tailoring + letters.
    name = profile["candidate"]["name"] or "Candidate"
    contact = " | ".join(x for x in [
        payload.get("email", "").strip(),
        payload.get("phone", "").strip(),
        payload.get("home_location", "").strip(),
    ] if x)
    skills = ", ".join(profile["candidate"]["skills"])

    resume_body = (payload.get("resume") or "").strip()
    resume_md = "---\nname: {0}\ncontact: {1}\n---\n\n## Summary\n{2}\n".format(
        name, contact or "add contact info", profile["candidate"]["summary"] or "Add a short summary.")
    if resume_body:
        resume_md += "\n" + resume_body + "\n"
    else:
        resume_md += "\n## Experience\n### Role — Employer (years)\n- Add a real, specific accomplishment.\n"
    if skills:
        resume_md += "\n## Skills\n{0}\n".format(skills)
    rpath = PROFILE_DIR / "resume.md"
    if _archive_existing(rpath):
        backed_up.append(str(rpath) + ".bak")
    _write_md(rpath, resume_md)
    wrote.append(str(rpath))

    voice = (payload.get("voice") or "").strip()
    _write_md(PROFILE_DIR / "voice_real.md",
              "# Real writing voice\n\n" + (voice or
              "Paste a few paragraphs the candidate actually wrote (an email, a "
              "note) so cover letters sound like them.\n"))
    wrote.append(str(PROFILE_DIR / "voice_real.md"))

    bank = (payload.get("experience_bank") or "").strip()
    _write_md(PROFILE_DIR / "experience_bank.md",
              "# Experience bank\n\n" + (bank or
              "Expanded bullets, quantified wins, and references the tailor may "
              "draw on — extra true material beyond the one-page resume.\n"))
    wrote.append(str(PROFILE_DIR / "experience_bank.md"))

    return {"wrote": wrote, "backed_up": backed_up,
            "employers": len(employers),
            "sources": build_sources(employers) if employers else None}


# --- The web app ----------------------------------------------------------

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>apply-assistant · onboarding</title>
<style>
  :root { --bg:#f6f7f9; --card:#fff; --ink:#1c2530; --muted:#6b7480;
          --line:#e2e6eb; --accent:#2f6fed; --accent-ink:#fff; --ok:#127a4a; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#12151a; --card:#1b2028; --ink:#e6eaef; --muted:#98a2b0;
            --line:#2a313b; --accent:#4b86ff; --accent-ink:#fff; --ok:#4cc38a; } }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
         font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
  .wrap { max-width:640px; margin:0 auto; padding:32px 20px 80px; }
  h1 { font-size:22px; margin:0 0 4px; }
  .sub { color:var(--muted); margin:0 0 24px; }
  .steps { display:flex; gap:6px; margin:0 0 20px; }
  .steps div { flex:1; height:4px; border-radius:2px; background:var(--line); }
  .steps div.on { background:var(--accent); }
  .card { background:var(--card); border:1px solid var(--line); border-radius:14px;
          padding:22px; margin-bottom:16px; }
  .card h2 { font-size:16px; margin:0 0 2px; }
  .card p.hint { color:var(--muted); margin:0 0 16px; font-size:13px; }
  label { display:block; font-weight:600; font-size:13px; margin:14px 0 5px; }
  label .opt { font-weight:400; color:var(--muted); }
  input[type=text], input[type=number], input[type=email], textarea, select {
    width:100%; padding:9px 11px; border:1px solid var(--line); border-radius:9px;
    background:var(--bg); color:var(--ink); font:inherit; }
  textarea { resize:vertical; min-height:70px; }
  .row { display:flex; gap:12px; }
  .row > div { flex:1; }
  .check { display:flex; align-items:center; gap:8px; margin-top:14px; }
  .check input { width:auto; }
  .check label { margin:0; }
  .emp { display:flex; gap:8px; margin-bottom:8px; }
  .emp input { flex:1; }
  .emp button { border:1px solid var(--line); background:var(--bg); color:var(--muted);
    border-radius:9px; width:38px; cursor:pointer; font-size:18px; }
  .addbtn { border:1px dashed var(--line); background:none; color:var(--accent);
    border-radius:9px; padding:8px 12px; cursor:pointer; font:inherit; }
  .nav { display:flex; justify-content:space-between; margin-top:8px; }
  button.primary { background:var(--accent); color:var(--accent-ink); border:none;
    border-radius:10px; padding:11px 22px; font:inherit; font-weight:600; cursor:pointer; }
  button.ghost { background:none; border:1px solid var(--line); color:var(--ink);
    border-radius:10px; padding:11px 18px; font:inherit; cursor:pointer; }
  button:disabled { opacity:.4; cursor:default; }
  .hidden { display:none; }
  .tips { color:var(--muted); font-size:13px; margin:0 0 10px; padding-left:20px; }
  .tips li { margin:2px 0; }
  .err { color:#b4392c; font-size:13px; margin-top:14px; display:none; }
  .err.on { display:block; }
  @media (prefers-color-scheme: dark) { .err { color:#f08b7e; } }
  .done { text-align:center; padding:20px 0; }
  .done .big { font-size:40px; }
  .done ul { text-align:left; display:inline-block; color:var(--muted); font-size:13px; }
  code { background:var(--bg); border:1px solid var(--line); border-radius:6px;
    padding:1px 6px; font-size:13px; }
</style>
</head>
<body>
<div class="wrap">
  <h1>Welcome<span id="who"></span></h1>
  <p class="sub">Four steps, about ten minutes. Defaults are set for the South Bay — change anything that's wrong. Step 3 asks for your resume and a few emails you've written, so it helps to have those open.</p>
  <div class="steps"><div class="on"></div><div></div><div></div><div></div></div>

  <form id="f">
    <!-- Step 1 -->
    <section class="card step" data-step="0">
      <h2>About you</h2>
      <p class="hint">Who the engine is looking for.</p>
      <label>Full name</label>
      <input type="text" name="name" placeholder="Your full name">
      <div class="row">
        <div><label>Email <span class="opt">· optional</span></label><input type="email" name="email"></div>
        <div><label>Phone <span class="opt">· optional</span></label><input type="text" name="phone"></div>
      </div>
      <label>Where you're based <span class="opt">· for your resume header</span></label>
      <input type="text" name="home_location" value="San Jose, CA">
      <label>One- or two-line summary</label>
      <textarea name="summary" placeholder="e.g. Operations manager, 8 years, scaling support and logistics teams at Bay Area startups."></textarea>
      <label>Job titles you're targeting <span class="opt">· comma-separated</span></label>
      <input type="text" name="titles" placeholder="Operations Manager, Program Manager, Business Operations">
      <label>Key skills <span class="opt">· comma-separated</span></label>
      <p class="hint" style="margin:0 0 6px">These are matched word-for-word against real job postings, so use the words postings actually use — "roadmap", "cross-functional", "go-to-market". Tools you happen to know, or personal shorthand, will never appear in a posting and will drag every score down. Six to eight is about right.</p>
      <input type="text" name="skills" placeholder="Product strategy, roadmap, cross-functional, go-to-market, user research, A/B testing">
      <p class="hint" id="skillsCount"></p>
      <div class="row">
        <div><label>Years of experience</label><input type="number" name="years_experience" min="0" value="5"></div>
        <div><label>Your seniority</label>
          <select name="seniority">
            <option>junior</option><option selected>mid</option><option>senior</option>
            <option>staff</option><option>principal</option><option>director</option>
          </select></div>
      </div>
      <label>Work authorization</label>
      <input type="text" name="work_authorization" value="US citizen (no sponsorship needed)">
      <div class="check"><input type="checkbox" id="spon" name="needs_sponsorship"><label for="spon">I need visa sponsorship</label></div>
    </section>

    <!-- Step 2 -->
    <section class="card step hidden" data-step="1">
      <h2>What you want</h2>
      <p class="hint">These drive the filter that drops jobs before you ever see them.</p>
      <label>Locations you'll consider <span class="opt">· comma-separated</span></label>
      <textarea name="locations"></textarea>
      <div class="check"><input type="checkbox" id="remote" name="remote_ok" checked><label for="remote">Remote roles are fine</label></div>
      <div class="row">
        <div><label>Most junior title you'd take</label>
          <select name="seniority_floor">
            <option value="">no floor</option><option>junior</option><option selected>mid</option>
            <option>senior</option><option>staff</option></select></div>
        <div><label>Most senior title that fits</label>
          <select name="seniority_ceiling">
            <option value="">no ceiling</option><option>mid</option><option selected>senior</option>
            <option>staff</option><option>principal</option><option>director</option></select></div>
      </div>
      <label>Minimum base salary <span class="opt">· optional, whole dollars</span></label>
      <input type="number" name="comp_floor" placeholder="140000">
      <label>Role words that mean "not for me" <span class="opt">· comma-separated</span></label>
      <input type="text" name="exclude_role_keywords">
      <label>Hard dealbreakers anywhere in a posting <span class="opt">· comma-separated</span></label>
      <input type="text" name="exclude_keywords">
    </section>

    <!-- Step 3 -->
    <section class="card step hidden" data-step="2">
      <h2>Your material</h2>
      <p class="hint">This is the step that decides how good everything else is. Your resume is the fact source — nothing gets written about you that isn't in here. The writing samples are how the cover letters end up sounding like you instead of like a robot.</p>

      <label>Paste your resume</label>
      <textarea name="resume" style="min-height:150px" placeholder="Paste your full resume text. Formatting doesn't matter — bullets and line breaks can come out messy from a PDF and that's fine. Every tailored line traces back to something here; nothing gets invented."></textarea>
      <p class="hint" id="resumeCount"></p>

      <label>Writing samples in your own voice <span class="opt">· three or four</span></label>
      <p class="hint" style="margin:0 0 8px">Paste a few things you actually wrote — the easiest source is your sent mail. Separate them with a blank line. What works best:</p>
      <ul class="tips">
        <li>An email to a colleague explaining a decision</li>
        <li>A note where you disagreed with someone, or pushed back</li>
        <li>Anything with an opinion in it — not a status update</li>
      </ul>
      <textarea name="voice" style="min-height:150px" placeholder="Paste three or four, separated by a blank line. Don't clean them up — the typos and the shorthand are the point."></textarea>
      <p class="hint" id="voiceCount"></p>

      <label>Experience bank <span class="opt">· optional, extra true material</span></label>
      <textarea name="experience_bank" placeholder="Expanded bullets, quantified wins, references — anything true that didn't fit the one-page resume."></textarea>
      <div class="err" id="err3"></div>
    </section>

    <!-- Step 4 -->
    <section class="card step hidden" data-step="3">
      <h2>Target employers <span class="opt" style="font-weight:400;color:var(--muted);font-size:13px">· optional</span></h2>
      <p class="hint">Paste the careers-page URL of companies you want watched. Greenhouse / Lever / Ashby / Workday links are read for free; anything else needs a Firecrawl key. Leave blank and add jobs by hand later with <code>apply add &lt;url&gt;</code>.</p>
      <div id="emps"></div>
      <button type="button" class="addbtn" id="addemp">+ Add employer</button>
    </section>

    <div class="nav">
      <button type="button" class="ghost" id="back" disabled>Back</button>
      <button type="button" class="primary" id="next">Next</button>
    </div>
  </form>

  <section class="card done hidden" id="doneCard">
    <div class="big">✓</div>
    <h2>You're all set</h2>
    <p class="sub" id="doneMsg"></p>
    <ul id="wrote"></ul>
    <p class="hint" id="localNext">Next, in your terminal: <code>apply sweep</code> then <code>apply match</code>.</p>
  </section>
</div>

<script>
  var HOSTED = %%HOSTED%%, ENDPOINT = %%ENDPOINT%%;
  var step = 0, total = 4;
  var stepEls = document.querySelectorAll('.step');
  var bars = document.querySelectorAll('.steps div');
  var back = document.getElementById('back'), next = document.getElementById('next');
  var nameInput = document.querySelector('[name=name]');
  var locInput = document.querySelector('[name=locations]');
  var xrole = document.querySelector('[name=exclude_role_keywords]');
  var xkw = document.querySelector('[name=exclude_keywords]');

  locInput.value = %%LOCATIONS%%;
  xrole.value = %%XROLE%%;
  xkw.value = %%XKW%%;

  nameInput.addEventListener('input', function(){
    // Reads "Welcome" until they type, then "Welcome, <name>".
    var v = nameInput.value.trim();
    document.getElementById('who').textContent = v ? ', ' + v : '';
  });

  function show(){
    stepEls.forEach(function(s,i){ s.classList.toggle('hidden', i!==step); });
    bars.forEach(function(b,i){ b.classList.toggle('on', i<=step); });
    back.disabled = step===0;
    next.textContent = step===total-1 ? 'Finish' : 'Next';
  }
  back.onclick = function(){ if(step>0){ step--; show(); } };

  // Step 3 decides how good every tailored resume and letter turns out, so it
  // is gated rather than skippable — an empty fact source means nothing can be
  // written, and one thin sample means the letters read generic.
  var resumeEl = document.querySelector('[name=resume]');
  var voiceEl = document.querySelector('[name=voice]');
  var err3 = document.getElementById('err3');
  var MIN_RESUME = 200, MIN_VOICE = 150;

  function sampleCount(t){
    return t.split(/\n\s*\n/)
            .map(function(s){ return s.trim(); })
            .filter(function(s){ return s.length > 40; }).length;
  }
  function counts(){
    var r = resumeEl.value.trim();
    document.getElementById('resumeCount').textContent = !r.length ? ''
      : (r.length < MIN_RESUME
          ? 'That looks short for a full resume — paste the whole thing.'
          : '✓ ' + r.length + ' characters');
    var v = voiceEl.value.trim(), n = sampleCount(v);
    document.getElementById('voiceCount').textContent = !v.length ? ''
      : (n <= 1
          ? 'Reads as 1 sample — two or three more makes the letters noticeably better.'
          : '✓ ' + n + ' samples');
  }
  resumeEl.addEventListener('input', counts);
  voiceEl.addEventListener('input', counts);

  // Skills are the keyword list the matcher scores against, so show the count —
  // too few and coverage stays low no matter how good the fit actually is.
  var skillsEl = document.querySelector('[name=skills]');
  skillsEl.addEventListener('input', function(){
    var n = skillsEl.value.split(',').map(function(s){ return s.trim(); }).filter(Boolean).length;
    document.getElementById('skillsCount').textContent = !n ? ''
      : (n < 4 ? n + ' skill(s) — a few more gives the matcher more to work with.'
               : '✓ ' + n + ' skills');
  });

  function validate3(){
    if (resumeEl.value.trim().length < MIN_RESUME)
      return 'Please paste your resume before continuing — every tailored line is built from it, so there is nothing to work with without it.';
    if (voiceEl.value.trim().length < MIN_VOICE)
      return 'Please paste at least one real thing you wrote. Without it the cover letters come out generic — this is the single biggest difference in quality.';
    return null;
  }

  next.onclick = function(){
    if (err3) err3.classList.remove('on');
    if (step === 2) {
      var problem = validate3();
      if (problem) { err3.textContent = problem; err3.classList.add('on'); return; }
    }
    if(step<total-1){ step++; show(); } else { submit(); }
  };

  // Employer rows
  var emps = document.getElementById('emps');
  function addRow(){
    var row = document.createElement('div'); row.className='emp';
    row.innerHTML = '<input type="text" placeholder="Company name" class="en">'
      + '<input type="text" placeholder="https://careers-page-url" class="eu">'
      + '<button type="button" title="remove">&times;</button>';
    row.querySelector('button').onclick = function(){ row.remove(); };
    emps.appendChild(row);
  }
  document.getElementById('addemp').onclick = addRow;
  addRow();

  function submit(){
    next.disabled = true; back.disabled = true; next.textContent = 'Saving…';
    var fd = new FormData(document.getElementById('f'));
    var data = {};
    fd.forEach(function(v,k){ data[k] = v; });
    ['needs_sponsorship','remote_ok'].forEach(function(k){
      data[k] = document.querySelector('[name='+k+']').checked;
    });
    var employers = [];
    document.querySelectorAll('.emp').forEach(function(r){
      var n = r.querySelector('.en').value.trim();
      var u = r.querySelector('.eu').value.trim();
      if(u) employers.push({name:n, url:u});
    });
    data.employers = employers;

    fetch(ENDPOINT, {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(data)})
      .then(function(r){
        if(!r.ok) throw new Error('server said ' + r.status);
        return r.json();
      })
      .then(function(res){
        if(res.error) throw new Error(res.error);
        document.getElementById('f').classList.add('hidden');
        document.querySelector('.nav').classList.add('hidden');
        document.querySelector('.steps').classList.add('hidden');
        document.getElementById('doneCard').classList.remove('hidden');
        var empCount = res.employers != null ? res.employers : employers.length;
        var extra = empCount ? (empCount + ' target employer(s) saved.') : 'No employers yet — they can be added later.';
        if(HOSTED){
          // The candidate's browser never touches the filesystem — the answers
          // sit in the queue until the pipeline host pulls them down.
          document.getElementById('doneMsg').textContent =
            'Your answers are in. ' + extra + ' Nothing else to do — Mark picks it up from here.';
          document.getElementById('localNext').classList.add('hidden');
        } else {
          document.getElementById('doneMsg').textContent = 'Your profile is written. ' + extra;
          var ul = document.getElementById('wrote');
          (res.wrote || []).forEach(function(p){ var li=document.createElement('li'); li.textContent=p; ul.appendChild(li); });
          fetch('/shutdown', {method:'POST'});
        }
      })
      .catch(function(e){
        next.disabled=false; back.disabled=false; next.textContent='Finish';
        alert('Save failed: ' + e.message + '\n\nNothing was lost — this page still has your answers. Try Finish again.');
      });
  }
  show();
</script>
</body>
</html>
"""


def _render_page(hosted=False):
    """The onboarding form. One template, two homes.

    Local (``hosted=False``): served on localhost, posts to ``/save``, writes
    the files directly, then shuts the server down.

    Hosted (``hosted=True``): baked into ``site/onboard.html`` and deployed
    behind the Desk's password gate so the candidate can fill it in remotely,
    on their own time. It posts to ``/api/onboard``, which parks the answers in
    Vercel Blob; ``apply onboard --fetch`` pulls them down and writes the same
    files through the same ``save_all()``. Same form, same result, one source.
    """
    endpoint = "/api/onboard" if hosted else "/save"
    return (PAGE
            .replace("%%LOCATIONS%%", json.dumps(", ".join(SOUTH_BAY_LOCATIONS)))
            .replace("%%XROLE%%", json.dumps(", ".join(DEFAULT_EXCLUDE_ROLE)))
            .replace("%%XKW%%", json.dumps(", ".join(DEFAULT_EXCLUDE_KW)))
            .replace("%%HOSTED%%", "true" if hosted else "false")
            .replace("%%ENDPOINT%%", json.dumps(endpoint)))


def emit_html(path, hosted=True):
    """Write the standalone form to `path` (deploy.sh bakes site/onboard.html).

    Generated at deploy time rather than kept as a second copy, so the hosted
    form can never drift from the local one.
    """
    from pathlib import Path

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_render_page(hosted=hosted))
    return str(p)


# --- Remote submissions ---------------------------------------------------

BLOB_PREFIX = "onboard/"


def read_submissions(token=None):
    """List queued onboarding submissions, newest last.

    Reads Vercel Blob directly with BLOB_READ_WRITE_TOKEN — the same path
    publish.py uses, so no Vercel CLI and no site password are needed here.
    """
    import requests

    from .publish import BLOB_API, _blob_token

    token = token or _blob_token()
    if not token:
        raise RuntimeError(
            "no BLOB_READ_WRITE_TOKEN — set it in .env to pull remote submissions")
    r = requests.get(BLOB_API, params={"prefix": BLOB_PREFIX, "limit": "100"},
                     headers={"Authorization": "Bearer " + token}, timeout=30)
    r.raise_for_status()
    out = []
    for b in r.json().get("blobs", []):
        try:
            # Private store: reading a blob URL needs the bearer token too.
            c = requests.get(b["url"], params={"v": str(int(time.time()))},
                             headers={"Authorization": "Bearer " + token}, timeout=30)
            if not c.ok:
                continue
            entry = c.json()
        except (requests.RequestException, ValueError):
            continue
        if isinstance(entry, dict) and isinstance(entry.get("payload"), dict):
            entry.setdefault("submittedAt", 0)
            out.append(entry)
    out.sort(key=lambda e: e.get("submittedAt") or 0)
    return out


def fetch_and_save(token=None, index=None):
    """Pull the newest remote submission and write the local profile files.

    Returns (report, entry). Raises if nothing has been submitted yet.
    """
    subs = read_submissions(token=token)
    if not subs:
        raise RuntimeError("no submissions in the queue yet")
    entry = subs[index] if index is not None else subs[-1]
    report = save_all(entry["payload"])

    # A pasted resume is whatever the candidate had lying around; tailoring
    # needs roles and bullets as discrete objects. Restructure it now, once,
    # rather than failing per-job later. Purely structural and validated —
    # if it can't be done honestly the raw paste stays.
    try:
        from .normalize_resume import normalize_file

        norm = normalize_file(verbose=False)
        report["normalized"] = norm
    except Exception as e:  # noqa: BLE001 - never block a fetch on this
        report["normalized"] = {"ok": False, "errors": [str(e)[:120]]}
    return report, entry


class _Handler(BaseHTTPRequestHandler):
    server_version = "apply-onboard/1.0"

    def log_message(self, *args):  # quiet — no per-request noise
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, _render_page())
        else:
            self._send(404, "not found")

    def do_POST(self):
        if self.path == "/save":
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8"))
                report = save_all(payload)
                self._send(200, json.dumps(report), "application/json")
                print("\n  Saved profile for: {0}".format(
                    payload.get("name", "").strip() or "(unnamed)"))
                for p in report["wrote"]:
                    print("    wrote   {0}".format(p))
                for b in report["backed_up"]:
                    print("    backup  {0}".format(b))
            except Exception as e:  # noqa: BLE001 - report any failure to the form
                self._send(500, json.dumps({"error": str(e)}), "application/json")
                print("  !! save failed: {0}".format(e))
        elif self.path == "/shutdown":
            self._send(200, json.dumps({"ok": True}), "application/json")
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        else:
            self._send(404, "not found")


def run_onboard(port=8765, open_browser=True):
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    url = "http://localhost:{0}".format(port)
    print("Onboarding app running at {0}".format(url))
    print("Fill it in, hit Finish, and the config files get written here.")
    print("(Ctrl-C to stop early.)\n")
    if open_browser:
        threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
    print("\nOnboarding complete. Next: `apply sweep` then `apply match`.")
