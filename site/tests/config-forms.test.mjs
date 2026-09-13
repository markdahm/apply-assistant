// The structured forms' pure halves: parse and compose.
//
//   cd site && npm test
//
// The resume form has one job that matters: whatever a person types into its
// boxes must come out in the exact shape apply_assistant/resume_doc.parse_resume
// reads — em dash between role and employer, dates in parentheses, "- " bullets,
// skills joined with " • ". So the tests here round-trip a resume in that shape
// and assert compose(parse(x)) is a fixed point, then assert the same for the
// two JSON forms, including that a key the form does not know about survives.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

// Run in THIS realm, not a fresh context: deepEqual checks prototypes, and an
// array made in another vm context has a different Array.prototype, so every
// structural assertion would fail on "same structure but not reference-equal".
const src = readFileSync(new URL('../config-forms.js', import.meta.url), 'utf8');
vm.runInThisContext(src);
const F = globalThis.DeskForms;

const RESUME = [
  '**Ann Example**',
  'ann@example.com | 4085551234 | Gilroy, CA',
  '',
  '## Summary',
  'A certified Internal ISO Auditor who is detail-oriented.',
  '',
  '## Experience',
  '### Food Safety Quality Assurance Coordinator — Example Farms (02/2025 – Present)',
  '- Ensure timely management of the hold-and-release process.',
  '- Serve as the primary point of contact for quality questions.',
  '### Quality Assurance Technician — LeanCo LLC (08/2023 – 02/2025)',
  '- Conduct process audits for complaints and gap analysis',
  '',
  '## Education',
  'San José State University 09/19 – 12/21',
  'Bachelor of Science: Computer Science',
  'Certifications',
  'Internal ISO Auditor 08/23',
  '',
  '## Skills',
  'Audits • HACCP • GMPs • Document Control',
  '',
].join('\n');

test('resume: parse reads the pipeline shape into name, contact, sections, roles and bullets', () => {
  const m = F['resume.md'].parse(RESUME);
  assert.equal(m.name, 'Ann Example');
  assert.equal(m.contact, 'ann@example.com | 4085551234 | Gilroy, CA');
  assert.deepEqual(m.sections.map((s) => s.title), ['Summary', 'Experience', 'Education', 'Skills']);
  const exp = m.sections[1];
  assert.equal(exp.jobs.length, 2);
  assert.deepEqual(exp.jobs[0], {
    role: 'Food Safety Quality Assurance Coordinator', org: 'Example Farms', dates: '02/2025 – Present',
    bullets: ['Ensure timely management of the hold-and-release process.', 'Serve as the primary point of contact for quality questions.'],
  });
  assert.deepEqual(m.sections[3].lines, ['Audits', 'HACCP', 'GMPs', 'Document Control'], 'skills split on the bullet separator');
  assert.deepEqual(m.sections[2].lines, ['San José State University 09/19 – 12/21', 'Bachelor of Science: Computer Science', 'Certifications', 'Internal ISO Auditor 08/23']);
});

test('resume: compose(parse(x)) is a fixed point on the pipeline shape', () => {
  const once = F['resume.md'].compose(F['resume.md'].parse(RESUME));
  assert.equal(once, RESUME);
  assert.equal(F['resume.md'].compose(F['resume.md'].parse(once)), once);
});

test('resume: compose writes the load-bearing punctuation whatever the boxes held', () => {
  const m = { name: 'A B', contact: 'a@b.c', sections: [
    { title: 'Experience', jobs: [{ role: ' QA Tech ', org: 'Co', dates: '2020 – 2021', bullets: ['did x'] }, { role: 'Intern', org: '', dates: '', bullets: [] }], lines: [] },
    { title: 'Skills', jobs: [], lines: ['a', 'b'] },
  ] };
  const text = F['resume.md'].compose(m);
  assert.match(text, /^### {2}QA Tech {2}— Co \(2020 – 2021\)$/m, 'em dash and parentheses');
  assert.match(text, /^### Intern$/m, 'a role with no employer or dates still gets its header');
  assert.match(text, /^- did x$/m);
  assert.match(text, /^a • b$/m);
});

test('resume: a role header without an em dash or dates still parses', () => {
  const m = F['resume.md'].parse('**N**\n\n## Experience\n### Just A Title\n- one\n');
  assert.deepEqual(m.sections[0].jobs[0], { role: 'Just A Title', org: '', dates: '', bullets: ['one'] });
});

test('resume: the structural helpers add and remove roles and sections', () => {
  const m = F['resume.md'].parse(RESUME);
  F['resume.md'].addJob(m, 1);
  assert.equal(m.sections[1].jobs.length, 3);
  F['resume.md'].removeJob(m, 1, 2);
  assert.equal(m.sections[1].jobs.length, 2);
  F['resume.md'].addSection(m, 'jobs');
  assert.equal(m.sections[4].title, 'Experience');
  assert.equal(m.sections[4].jobs.length, 1);
  F['resume.md'].removeSection(m, 4);
  assert.equal(m.sections.length, 4);
});

// ── profile.json ────────────────────────────────────────────────────────────

const PROFILE = JSON.stringify({
  candidate: { name: 'Ann', titles: ['QA Lead'], skills: ['HACCP'], years_experience: 5, seniority: 'mid', extra_candidate_key: 'kept' },
  preferences: { target_role_keywords: ['food safety'], locations: ['gilroy'], remote_ok: true, comp_floor: 65000, exclude_keywords: [] },
  unknown_top_level: { a: 1 },
}, null, 2);

test('profile: parse exposes both sections and compose keeps keys the form does not know', () => {
  const m = F['profile.json'].parse(PROFILE);
  assert.equal(m.candidate.name, 'Ann');
  assert.deepEqual(m.preferences.locations, ['gilroy']);
  m.preferences.locations = ['gilroy', 'salinas'];
  m.candidate.name = 'Ann Example';
  const out = JSON.parse(F['profile.json'].compose(m, PROFILE));
  assert.deepEqual(out.preferences.locations, ['gilroy', 'salinas']);
  assert.equal(out.candidate.name, 'Ann Example');
  assert.equal(out.candidate.extra_candidate_key, 'kept', 'unknown candidate key survives');
  assert.deepEqual(out.unknown_top_level, { a: 1 }, 'unknown top-level key survives');
  assert.equal(out.preferences.remote_ok, true);
});

test('profile: compose on a blank original still yields the two required sections', () => {
  const out = JSON.parse(F['profile.json'].compose({ candidate: { name: 'X' }, preferences: {} }, ''));
  assert.deepEqual(Object.keys(out).sort(), ['candidate', 'preferences']);
});

// ── sources.json ────────────────────────────────────────────────────────────

const SOURCES = JSON.stringify({
  _note: 'note kept',
  greenhouse: ['stripe'], lever: [], ashby: [], workable: [], smartrecruiters: [], workday: [],
  firecrawl_boards: [{ url: 'https://x.com/careers', name: 'X Co' }, { url: 'https://y.com/jobs', name: 'https://y.com/jobs' }],
  jsearch_queries: ['food safety in Salinas, CA'],
});

test('sources: boards become "Name | URL" lines and back, a nameless board stays nameless', () => {
  const m = F['sources.json'].parse(SOURCES);
  assert.deepEqual(m.boards, ['X Co | https://x.com/careers', 'https://y.com/jobs']);
  assert.deepEqual(m.greenhouse, ['stripe']);
  m.boards.push('New Farm | https://newfarm.example/careers');
  m.jsearch_queries.push('HACCP in Gilroy, CA');
  const out = JSON.parse(F['sources.json'].compose(m, SOURCES));
  assert.deepEqual(out.firecrawl_boards, [
    { url: 'https://x.com/careers', name: 'X Co' },
    { url: 'https://y.com/jobs', name: 'https://y.com/jobs' },
    { url: 'https://newfarm.example/careers', name: 'New Farm' },
  ]);
  assert.deepEqual(out.jsearch_queries, ['food safety in Salinas, CA', 'HACCP in Gilroy, CA']);
  assert.equal(out._note, 'note kept');
});

test('sources: compose(parse(x)) round-trips the pipeline\'s own file shape', () => {
  const once = F['sources.json'].compose(F['sources.json'].parse(SOURCES), SOURCES);
  assert.deepEqual(JSON.parse(once), JSON.parse(SOURCES));
});

test('the page loads the forms script and no other file ships the form logic', () => {
  const page = readFileSync(new URL('../config.html', import.meta.url), 'utf8');
  assert.match(page, /<script src="\.\/config-forms\.js"><\/script>/);
  assert.match(page, /window\.DeskForms/, 'the page must render through DeskForms');
  assert.match(page, /form\.render\(/);
  assert.match(page, /form\.compose\(/, 'saves must go through compose, never a hand-built string');
});
