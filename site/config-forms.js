// Structured forms for the Settings page.
//
// Each of the three structured files has a form: parse the file into a model,
// render the model as titled sections of text boxes, read the boxes back into
// a model, and compose the file text again. compose() always starts from the
// ORIGINAL text where that is possible (the two JSON files), so a key the form
// does not know about survives a save untouched.
//
// resume.md is composed in exactly the shape the pipeline's parser reads
// (apply_assistant/resume_doc.parse_resume and the normalizer's target):
//
//     **Name**
//     email | phone | city
//
//     ## Experience
//     ### Role — Employer (dates)
//     - bullet
//
//     ## Skills
//     skill • skill
//
// The em dash, the parentheses and the "- " bullets are load-bearing there,
// which is the reason a form exists at all: a text box per field cannot lose
// them the way a free-text edit can.
//
// Plain script, no build step. Exposes DeskForms on the global. Pure parts
// (parse, compose) are tested by node in site/tests/config-forms.test.mjs;
// render/read need a DOM and are exercised in the browser.

(function (root) {
  'use strict';

  var esc = function (s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); };
  var lines = function (s) { return String(s || '').split(/\r?\n/).map(function (x) { return x.trim(); }).filter(Boolean); };
  var joinLines = function (arr) { return (arr || []).join('\n'); };
  var SENIORITY = ['intern', 'junior', 'mid', 'senior', 'staff', 'principal', 'director', 'vp', 'exec'];

  // ── Field renderers ──────────────────────────────────────────────────────
  function fieldHtml(f, value) {
    var id = 'f_' + f.key.replace(/\W/g, '_');
    var help = f.help ? '<div class="help">' + esc(f.help) + '</div>' : '';
    var label = '<label for="' + id + '">' + esc(f.label) + '</label>';
    if (f.type === 'lines') {
      return '<div class="field">' + label + help + '<textarea id="' + id + '" data-f="' + esc(f.key) + '" rows="' + (f.rows || 5) + '" placeholder="one per line">' + esc(joinLines(value)) + '</textarea></div>';
    }
    if (f.type === 'text-long') {
      return '<div class="field">' + label + help + '<textarea id="' + id + '" data-f="' + esc(f.key) + '" rows="' + (f.rows || 4) + '">' + esc(value == null ? '' : value) + '</textarea></div>';
    }
    if (f.type === 'bool') {
      return '<div class="field check"><input type="checkbox" id="' + id + '" data-f="' + esc(f.key) + '"' + (value ? ' checked' : '') + '>' + label + help + '</div>';
    }
    if (f.type === 'select') {
      return '<div class="field">' + label + help + '<select id="' + id + '" data-f="' + esc(f.key) + '"><option value="">—</option>' + f.options.map(function (o) { return '<option value="' + esc(o) + '"' + (o === value ? ' selected' : '') + '>' + esc(o) + '</option>'; }).join('') + '</select></div>';
    }
    if (f.type === 'number') {
      return '<div class="field"><label for="' + id + '">' + esc(f.label) + '</label>' + help + '<input type="number" id="' + id + '" data-f="' + esc(f.key) + '" value="' + esc(value == null ? '' : value) + '"></div>';
    }
    return '<div class="field">' + label + help + '<input type="text" id="' + id + '" data-f="' + esc(f.key) + '" value="' + esc(value == null ? '' : value) + '"></div>';
  }

  function readFields(rootEl, fields, into) {
    fields.forEach(function (f) {
      var el = rootEl.querySelector('[data-f="' + f.key + '"]');
      if (!el) return;
      if (f.type === 'lines') into[f.key] = lines(el.value);
      else if (f.type === 'bool') into[f.key] = !!el.checked;
      else if (f.type === 'number') into[f.key] = el.value === '' ? null : Number(el.value);
      else into[f.key] = el.value;
    });
    return into;
  }

  function section(title, inner, note) {
    return '<section class="fsec"><h3>' + esc(title) + '</h3>' + (note ? '<div class="note">' + esc(note) + '</div>' : '') + inner + '</section>';
  }

  // ── profile.json ─────────────────────────────────────────────────────────
  var CAND = [
    { key: 'name', label: 'Full name', help: 'This is what letters are signed with.' },
    { key: 'summary', label: 'Summary', type: 'text-long', rows: 4 },
    { key: 'titles', label: 'Job titles you hold or want', type: 'lines', rows: 6 },
    { key: 'skills', label: 'Skills', type: 'lines', rows: 6, help: 'The tailor may reorder these and drop some. It never adds to the list.' },
    { key: 'years_experience', label: 'Years of experience', type: 'number' },
    { key: 'seniority', label: 'Your level', type: 'select', options: SENIORITY },
    { key: 'work_authorization', label: 'Work authorization', help: 'e.g. US citizen (no sponsorship needed)' },
  ];
  var PREF = [
    { key: 'target_role_keywords', label: 'Target role words', type: 'lines', rows: 8, help: 'A job title must contain one of these to get through the filter. Word-matched and accent-proof; "quality assurance specialist" also admits "quality assurance".' },
    { key: 'exclude_role_keywords', label: 'Title words that rule a job out', type: 'lines', rows: 4 },
    { key: 'seniority_floor', label: 'Lowest level you want', type: 'select', options: SENIORITY },
    { key: 'seniority_ceiling', label: 'Highest level you want', type: 'select', options: SENIORITY },
    { key: 'locations', label: 'Places you will work', type: 'lines', rows: 8, help: 'Cities or regions, one per line. "United States" on a posting always passes.' },
    { key: 'remote_ok', label: 'Remote is fine', type: 'bool' },
    { key: 'comp_floor', label: 'Lowest pay you will consider (USD per year)', type: 'number', help: 'Only applied when a posting lists a top figure below this.' },
    { key: 'exclude_keywords', label: 'Words anywhere in a posting that rule it out', type: 'lines', rows: 5 },
    { key: 'needs_sponsorship', label: 'I need visa sponsorship', type: 'bool' },
  ];

  var profileForm = {
    parse: function (text) {
      var doc = {};
      try { doc = JSON.parse(text || '{}') || {}; } catch (e) { doc = {}; }
      return { candidate: Object.assign({}, doc.candidate || {}), preferences: Object.assign({}, doc.preferences || {}) };
    },
    render: function (m) {
      return section('Candidate', CAND.map(function (f) { return fieldHtml(f, m.candidate[f.key]); }).join(''))
        + section('Preferences', PREF.map(function (f) { return fieldHtml(f, m.preferences[f.key]); }).join(''), 'What the filter rejects before anyone scores it.');
    },
    read: function (rootEl, m) {
      var out = { candidate: Object.assign({}, m.candidate), preferences: Object.assign({}, m.preferences) };
      readFields(rootEl, CAND, out.candidate);
      readFields(rootEl, PREF, out.preferences);
      return out;
    },
    compose: function (m, original) {
      var doc = {};
      try { doc = JSON.parse(original || '{}') || {}; } catch (e) { doc = {}; }
      doc.candidate = Object.assign({}, doc.candidate || {}, m.candidate);
      doc.preferences = Object.assign({}, doc.preferences || {}, m.preferences);
      return JSON.stringify(doc, null, 2) + '\n';
    },
  };

  // ── sources.json ─────────────────────────────────────────────────────────
  var ATS = ['greenhouse', 'lever', 'ashby', 'workable', 'smartrecruiters', 'workday'];
  var ATS_HELP = {
    greenhouse: 'Board tokens, e.g. "stripe" from boards.greenhouse.io/stripe',
    lever: 'Company slugs from jobs.lever.co/<slug>',
    ashby: 'Company slugs from jobs.ashbyhq.com/<slug>',
    workable: 'Company slugs from apply.workable.com/<slug>',
    smartrecruiters: 'Company identifiers from careers.smartrecruiters.com/<id>',
    workday: 'Full Workday careers URLs',
  };

  function boardsToLines(boards) {
    return (boards || []).map(function (b) { return (b.name && b.name !== b.url ? b.name + ' | ' : '') + (b.url || ''); });
  }
  function linesToBoards(arr) {
    return lines(joinLines(arr)).map(function (l) {
      var parts = l.split('|').map(function (x) { return x.trim(); });
      if (parts.length >= 2) return { url: parts.slice(1).join(' | '), name: parts[0] };
      return { url: parts[0], name: parts[0] };
    }).filter(function (b) { return b.url; });
  }

  var sourcesForm = {
    parse: function (text) {
      var doc = {};
      try { doc = JSON.parse(text || '{}') || {}; } catch (e) { doc = {}; }
      var m = { boards: boardsToLines(doc.firecrawl_boards), jsearch_queries: (doc.jsearch_queries || []).slice() };
      ATS.forEach(function (k) { m[k] = (doc[k] || []).map(String); });
      return m;
    },
    render: function (m) {
      return section('Employers by careers system', ATS.map(function (k) {
        return fieldHtml({ key: k, label: k, type: 'lines', rows: 3, help: ATS_HELP[k] }, m[k]);
      }).join(''), 'These feeds are free. One entry per line.')
        + section('Careers pages scraped with Firecrawl', fieldHtml({ key: 'boards', label: 'Pages', type: 'lines', rows: 6, help: 'One per line as "Name | URL". Each scrape costs a credit; about half of hand-listed pages are JavaScript portals that return nothing.' }, m.boards))
        + section('JSearch phrases', fieldHtml({ key: 'jsearch_queries', label: 'Search phrases', type: 'lines', rows: 8, help: 'Used word for word against Google for Jobs (LinkedIn, Indeed, ZipRecruiter). Each phrase is two requests of a 200-per-month quota. Name the town where your industry is, not only where you live.' }, m.jsearch_queries));
    },
    read: function (rootEl, m) {
      var out = Object.assign({}, m);
      var fields = ATS.map(function (k) { return { key: k, type: 'lines' }; }).concat([{ key: 'boards', type: 'lines' }, { key: 'jsearch_queries', type: 'lines' }]);
      return readFields(rootEl, fields, out);
    },
    compose: function (m, original) {
      var doc = {};
      try { doc = JSON.parse(original || '{}') || {}; } catch (e) { doc = {}; }
      if (!doc._note) doc._note = 'Target employers. Public-ATS entries feed for free; firecrawl_boards need a FIRECRAWL_API_KEY.';
      ATS.forEach(function (k) { doc[k] = m[k] || []; });
      doc.firecrawl_boards = linesToBoards(m.boards);
      doc.jsearch_queries = m.jsearch_queries || [];
      return JSON.stringify(doc, null, 2) + '\n';
    },
  };

  // ── resume.md ────────────────────────────────────────────────────────────
  var SKILL_SEP = ' • ';

  function isJobsSection(sec) { return sec.jobs.length > 0 || /^experience$/i.test(sec.title); }
  function isSkills(sec) { return /^skills$/i.test(sec.title); }

  var resumeForm = {
    parse: function (text) {
      var doc = { name: '', contact: '', sections: [] }, sec = null, job = null;
      String(text || '').split(/\r?\n/).forEach(function (raw) {
        var s = raw.trim();
        if (!s) return;
        var m = /^\*\*(.+)\*\*$/.exec(s);
        if (m && !doc.name) { doc.name = m[1].trim(); return; }
        if (!doc.contact && doc.name && s[0] !== '#' && s.indexOf('|') >= 0) { doc.contact = s; return; }
        if (s.indexOf('## ') === 0) { sec = { title: s.slice(3).trim(), jobs: [], lines: [] }; doc.sections.push(sec); job = null; return; }
        if (s.indexOf('### ') === 0 && sec) {
          var head = s.slice(4).trim(), mm = /^(.*)\(([^)]*)\)\s*$/.exec(head);
          var where = mm ? mm[1].replace(/[\s,]+$/, '') : head, dates = mm ? mm[2].trim() : '';
          var cut = where.indexOf('—');
          job = { role: (cut >= 0 ? where.slice(0, cut) : where).trim(), org: (cut >= 0 ? where.slice(cut + 1) : '').trim(), dates: dates, bullets: [] };
          sec.jobs.push(job); return;
        }
        if (s.indexOf('- ') === 0 && job) { job.bullets.push(s.slice(2).trim()); return; }
        if (sec) {
          if (isSkills(sec)) sec.lines = sec.lines.concat(s.split(SKILL_SEP.trim()).map(function (x) { return x.trim(); }).filter(Boolean));
          else sec.lines.push(s);
        }
      });
      return doc;
    },
    render: function (m) {
      var h = section('Header', fieldHtml({ key: 'name', label: 'Full name' }, m.name)
        + fieldHtml({ key: 'contact', label: 'Contact line', help: 'email | phone | city — separated by " | "' }, m.contact));
      m.sections.forEach(function (sec, si) {
        var inner = '<div class="field"><label>Section title</label><input type="text" data-sec-title="' + si + '" value="' + esc(sec.title) + '"></div>';
        if (isJobsSection(sec)) {
          inner += sec.jobs.map(function (j, ji) {
            return '<div class="job" data-job="' + si + ':' + ji + '">'
              + '<div class="jobhead"><span>Role ' + (ji + 1) + '</span><button type="button" class="link" data-remove-job="' + si + ':' + ji + '">Remove</button></div>'
              + '<div class="field"><label>Job title</label><input type="text" data-j="role" value="' + esc(j.role) + '"></div>'
              + '<div class="field"><label>Employer</label><input type="text" data-j="org" value="' + esc(j.org) + '"></div>'
              + '<div class="field"><label>Dates</label><input type="text" data-j="dates" value="' + esc(j.dates) + '" placeholder="02/2025 – Present"></div>'
              + '<div class="field"><label>What you did</label><div class="help">One bullet per line. Every tailored line must trace back to one of these.</div><textarea data-j="bullets" rows="' + Math.max(4, j.bullets.length + 1) + '">' + esc(joinLines(j.bullets)) + '</textarea></div>'
              + '</div>';
          }).join('') + '<button type="button" class="link" data-add-job="' + si + '">+ Add a role</button>';
        } else if (isSkills(sec)) {
          inner += '<div class="field"><label>Skills</label><div class="help">One per line. Saved as a single line separated by " • ".</div><textarea data-sec-lines="' + si + '" rows="' + Math.max(4, sec.lines.length + 1) + '">' + esc(joinLines(sec.lines)) + '</textarea></div>';
        } else {
          inner += '<div class="field"><label>Text</label><textarea data-sec-lines="' + si + '" rows="' + Math.max(4, sec.lines.length + 1) + '">' + esc(joinLines(sec.lines)) + '</textarea></div>';
        }
        inner += '<button type="button" class="link danger" data-remove-sec="' + si + '">Remove this section</button>';
        h += section(sec.title || ('Section ' + (si + 1)), inner);
      });
      h += '<div class="addrow"><button type="button" class="link" data-add-sec="text">+ Add a text section</button> <button type="button" class="link" data-add-sec="jobs">+ Add an experience section</button></div>';
      return h;
    },
    read: function (rootEl, m) {
      var out = { name: '', contact: '', sections: [] };
      var n = rootEl.querySelector('[data-f="name"]'), c = rootEl.querySelector('[data-f="contact"]');
      out.name = n ? n.value.trim() : m.name; out.contact = c ? c.value.trim() : m.contact;
      m.sections.forEach(function (sec, si) {
        var t = rootEl.querySelector('[data-sec-title="' + si + '"]');
        var ns = { title: t ? t.value.trim() : sec.title, jobs: [], lines: [] };
        if (isJobsSection(sec)) {
          sec.jobs.forEach(function (j, ji) {
            var el = rootEl.querySelector('[data-job="' + si + ':' + ji + '"]');
            if (!el) { ns.jobs.push(j); return; }
            ns.jobs.push({
              role: el.querySelector('[data-j="role"]').value.trim(),
              org: el.querySelector('[data-j="org"]').value.trim(),
              dates: el.querySelector('[data-j="dates"]').value.trim(),
              bullets: lines(el.querySelector('[data-j="bullets"]').value),
            });
          });
        } else {
          var ta = rootEl.querySelector('[data-sec-lines="' + si + '"]');
          ns.lines = ta ? lines(ta.value) : sec.lines;
        }
        out.sections.push(ns);
      });
      return out;
    },
    compose: function (m) {
      var out = [];
      if (m.name) out.push('**' + m.name + '**');
      if (m.contact) out.push(m.contact);
      m.sections.forEach(function (sec) {
        out.push('', '## ' + sec.title);
        if (isJobsSection(sec)) {
          sec.jobs.forEach(function (j) {
            out.push('### ' + j.role + (j.org ? ' — ' + j.org : '') + (j.dates ? ' (' + j.dates + ')' : ''));
            j.bullets.forEach(function (b) { out.push('- ' + b); });
          });
        } else if (isSkills(sec)) {
          out.push(sec.lines.join(SKILL_SEP));
        } else {
          sec.lines.forEach(function (l) { out.push(l); });
        }
      });
      return out.join('\n') + '\n';
    },
    // Structural edits re-render from the model; the page calls these, then render().
    addJob: function (m, si) { m.sections[si].jobs.push({ role: '', org: '', dates: '', bullets: [] }); return m; },
    removeJob: function (m, si, ji) { m.sections[si].jobs.splice(ji, 1); return m; },
    addSection: function (m, kind) { m.sections.push(kind === 'jobs' ? { title: 'Experience', jobs: [{ role: '', org: '', dates: '', bullets: [] }], lines: [] } : { title: 'New section', jobs: [], lines: [] }); return m; },
    removeSection: function (m, si) { m.sections.splice(si, 1); return m; },
  };

  // ── Settings → onboarding form ───────────────────────────────────────────
  // The five files, turned back into the answers the onboarding form asks for,
  // so the form can be re-sent without losing what was tuned on Settings. The
  // inverse of apply_assistant/onboard.save_all: tests/test_settings_roundtrip.py
  // feeds this output through the real build_profile / build_sources /
  // save_all and checks the files come back the same.

  var ATS_URL = {
    greenhouse: function (v) { return 'https://boards.greenhouse.io/' + v; },
    lever: function (v) { return 'https://jobs.lever.co/' + v; },
    ashby: function (v) { return 'https://jobs.ashbyhq.com/' + v; },
    workable: function (v) { return 'https://apply.workable.com/' + v; },
    smartrecruiters: function (v) { return 'https://careers.smartrecruiters.com/' + v; },
    // The value is "tenant/site"; the wdN host number is unknown from here and
    // does not affect how the URL is routed back.
    workday: function (v) { var p = String(v).split('/'); return /^https?:/.test(v) ? v : 'https://' + p[0] + '.wd5.myworkdayjobs.com/' + (p[1] || ''); },
  };

  function contactParts(line) {
    var out = { email: '', phone: '', home_location: '' };
    String(line || '').split('|').map(function (x) { return x.trim(); }).filter(Boolean).forEach(function (part) {
      if (!out.email && part.indexOf('@') > 0) out.email = part;
      else if (!out.phone && (part.replace(/\D/g, '').length >= 7) && !/[a-z]{3}/i.test(part)) out.phone = part;
      else if (!out.home_location) out.home_location = part;
    });
    return out;
  }

  function stripHeading(text) {
    var t = String(text || '').replace(/^\s*#\s[^\n]*\n+/, '');
    return t.trim();
  }

  // save_all() writes the resume as: front matter, "## Summary" from the
  // profile summary, the pasted body, then "## Skills" from the profile
  // skills. So the body handed back to the form is everything EXCEPT the name
  // and contact lines and those two sections — or they would come out twice.
  function resumeBodyForForm(text) {
    var m = resumeForm.parse(text);
    var keep = m.sections.filter(function (s) { return !/^(summary|skills)$/i.test(s.title); });
    return resumeForm.compose({ name: '', contact: '', sections: keep }).trim();
  }

  function employersFromSources(doc) {
    var out = [];
    ATS.forEach(function (k) {
      (doc[k] || []).forEach(function (v) { out.push({ name: String(v), url: ATS_URL[k](String(v)) }); });
    });
    (doc.firecrawl_boards || []).forEach(function (b) { if (b && b.url) out.push({ name: b.name && b.name !== b.url ? b.name : '', url: b.url }); });
    return out;
  }

  function toPayload(files) {
    var by = {};
    (files || []).forEach(function (f) { by[f.name] = f.content; });
    var prof = {}, src = {};
    try { prof = JSON.parse(by['profile.json'] || '{}') || {}; } catch (e) { prof = {}; }
    try { src = JSON.parse(by['sources.json'] || '{}') || {}; } catch (e) { src = {}; }
    var c = prof.candidate || {}, p = prof.preferences || {};
    var resume = resumeForm.parse(by['resume.md'] || '');
    var contact = contactParts(resume.contact);
    var csv = function (arr) { return (arr || []).join(', '); };
    return {
      name: c.name || resume.name || '',
      email: contact.email, phone: contact.phone, home_location: contact.home_location,
      summary: c.summary || '',
      titles: csv(c.titles),
      target_role_keywords: csv(p.target_role_keywords),
      skills: csv(c.skills),
      years_experience: c.years_experience == null ? '' : String(c.years_experience),
      seniority: c.seniority || '',
      work_authorization: c.work_authorization || '',
      needs_sponsorship: !!p.needs_sponsorship,
      locations: csv(p.locations),
      remote_ok: !!p.remote_ok,
      seniority_floor: p.seniority_floor || '',
      seniority_ceiling: p.seniority_ceiling || '',
      comp_floor: p.comp_floor == null ? '' : String(p.comp_floor),
      exclude_role_keywords: csv(p.exclude_role_keywords),
      exclude_keywords: csv(p.exclude_keywords),
      jsearch_queries: (src.jsearch_queries || []).join('\n'),
      resume: resumeBodyForForm(by['resume.md'] || ''),
      voice: stripHeading(by['voice_real.md']),
      experience_bank: stripHeading(by['experience_bank.md']),
      employers: employersFromSources(src),
    };
  }

  root.DeskForms = {
    'profile.json': profileForm, 'sources.json': sourcesForm, 'resume.md': resumeForm,
    toPayload: toPayload, contactParts: contactParts, resumeBodyForForm: resumeBodyForForm,
    employersFromSources: employersFromSources, _lines: lines,
  };
})(typeof globalThis !== 'undefined' ? globalThis : this);
