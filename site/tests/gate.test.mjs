// The gate, pinned by reading the source.
//
//   cd site && npm test
//
// These are the checks that catch the class of mistake the roster tests
// cannot: a handler that forgot to ask who is calling, a bare pathname that
// would put two candidates' data in one blob, a public path added by accident,
// an error code the login page cannot explain. Each one is a scan of the code
// as written, so each strips comments first — three earlier scans in Mark's
// projects matched the comment explaining the rule they were checking.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, existsSync } from 'node:fs';
import { PUBLIC_PATHS, decide } from '../api/_lib/who.mjs';
import { signSession, SESSION_COOKIE } from '../api/_lib/session.mjs';

const site = (p) => new URL('../' + p, import.meta.url);
const read = (p) => readFileSync(site(p), 'utf8');
// Strip // and /* */ comments so an assertion measures code, not prose.
const code = (src) => src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:'"])\/\/[^\n]*/g, '$1');

// Every function in api/ that reads or writes candidate data.
const DATA_HANDLERS = ['api/jobs.js', 'api/status.js', 'api/inbox.js', 'api/letter.js', 'api/onboard.js'];

const ENV = { DESK_OPERATORS: 'mark@example.com', DESK_CANDIDATES: 'ann@example.com', DESK_SESSION_SECRET: 's3cret' };
const cookieFor = async (email) => `${SESSION_COOKIE}=${await signSession(ENV.DESK_SESSION_SECRET, email)}`;

// ── decide(): the gate's behaviour, driven with real cookies ─────────────────

test('the public paths are exactly the sign-in flow and the login page', () => {
  assert.deepEqual([...PUBLIC_PATHS].sort(), ['/api/auth/callback/google', '/api/auth/google', '/login']);
});

test('public paths pass with no cookie; nothing underneath them does', async () => {
  for (const p of PUBLIC_PATHS) {
    assert.deepEqual(await decide({ pathname: p, cookieHeader: '', env: ENV }), { action: 'pass' }, p);
  }
  // Exact match, not prefix: a path that merely starts with a public one is gated.
  assert.equal((await decide({ pathname: '/login/anything', cookieHeader: '', env: ENV })).action, 'redirect');
  assert.equal((await decide({ pathname: '/api/auth/google/extra', cookieHeader: '', env: ENV })).action, 'unauthorized');
});

test('a signed-in candidate and a signed-in operator both pass', async () => {
  for (const who of ['ann@example.com', 'mark@example.com']) {
    assert.deepEqual(await decide({ pathname: '/desk', cookieHeader: await cookieFor(who), env: ENV }), { action: 'pass' }, who);
    assert.deepEqual(await decide({ pathname: '/api/jobs', cookieHeader: await cookieFor(who), env: ENV }), { action: 'pass' }, who);
  }
});

test('no session: a page is sent to sign in and told where to come back to', async () => {
  assert.deepEqual(await decide({ pathname: '/onboard', search: '?x=1', cookieHeader: '', env: ENV }),
    { action: 'redirect', to: '/login?next=' + encodeURIComponent('/onboard?x=1') });
  assert.deepEqual(await decide({ pathname: '/', cookieHeader: '', env: ENV }), { action: 'redirect', to: '/login' });
});

test('no session: an API call gets a 401, never a login page', async () => {
  for (const p of ['/api/jobs', '/api/status', '/api/me', '/api/onboard', '/api/auth/signout']) {
    assert.deepEqual(await decide({ pathname: p, cookieHeader: '', env: ENV }), { action: 'unauthorized' }, p);
  }
});

test('a valid session for someone taken off the roster is refused on the next request', async () => {
  const cookie = await cookieFor('ann@example.com');
  assert.equal((await decide({ pathname: '/desk', cookieHeader: cookie, env: ENV })).action, 'pass');
  const gone = { ...ENV, DESK_CANDIDATES: '' };
  assert.equal((await decide({ pathname: '/desk', cookieHeader: cookie, env: gone })).action, 'redirect');
});

test('an empty roster or a missing secret closes the door rather than opening it', async () => {
  const cookie = await cookieFor('ann@example.com');
  assert.equal((await decide({ pathname: '/desk', cookieHeader: cookie, env: { DESK_SESSION_SECRET: 's3cret' } })).action, 'redirect');
  assert.equal((await decide({ pathname: '/desk', cookieHeader: cookie, env: { ...ENV, DESK_SESSION_SECRET: '' } })).action, 'redirect');
});

test('the redirect target cannot be steered off-site', async () => {
  // A hostile path reaches decide() only via the URL, so the worst case is a
  // path that LOOKS like an authority. It must come back as '/'.
  const d = await decide({ pathname: '//evil.com', cookieHeader: '', env: ENV });
  assert.equal(d.to, '/login?next=' + encodeURIComponent('/'));
});

// ── The adapter and the handlers, pinned by reading the source ───────────────

test('middleware.js is only an adapter over decide()', () => {
  const src = code(read('middleware.js'));
  assert.match(src, /decide\(/, 'middleware must go through decide()');
  assert.ok(!/verifySession|identify\(|PUBLIC_PATHS|DESK_PASSWORD/.test(src),
    'logic has crept into middleware.js, where the tests cannot see it');
});

test('the password login is gone everywhere', () => {
  assert.ok(!existsSync(site('api/login.js')), 'api/login.js must not come back');
  for (const f of ['middleware.js', 'login.html', ...DATA_HANDLERS, 'api/me.js', 'api/auth/google.js',
    'api/auth/callback/google.js', 'api/auth/signout.js', 'api/_who.js']) {
    assert.ok(!/DESK_PASSWORD/.test(code(read(f))), `${f} still reads DESK_PASSWORD`);
  }
  assert.ok(!/type="password"/.test(read('login.html')), 'login.html still has a password box');
});

test('every data handler resolves the candidate before touching a blob', () => {
  for (const f of DATA_HANDLERS) {
    const src = code(read(f));
    assert.match(src, /requireCandidate\(req,\s*res\)/, `${f} does not call requireCandidate`);
    assert.match(src, /if\s*\(!c\)\s*return;/, `${f} does not stop when requireCandidate refuses`);
    // The refusal has to happen BEFORE the first blob call.
    const gate = src.indexOf('requireCandidate(');
    for (const op of ['readFixed(', 'list(', 'put(']) {
      const at = src.indexOf(op);
      if (at >= 0) assert.ok(at > gate, `${f}: ${op} appears before the candidate check`);
    }
  }
});

test('no data handler names a bare blob pathname — every one goes through c.path()', () => {
  // The flat names of the single-candidate era. Any of these as a string
  // literal handed to a blob call is one candidate's data in a shared slot.
  const flat = /(['"])(desk-data-live\.json|status\.json|onboard\/|inbox\/|letter-requests\/)\1/;
  for (const f of DATA_HANDLERS) {
    const src = code(read(f));
    for (const line of src.split('\n')) {
      if (!flat.test(line)) continue;
      assert.ok(/c\.path\(|const NAME\s*=/.test(line),
        `${f}: bare pathname on a line that is not a c.path() call or the NAME constant:\n    ${line.trim()}`);
    }
    for (const op of ['readFixed(', 'list({ prefix:', 'put(']) {
      for (const line of src.split('\n')) {
        if (line.includes(op)) assert.match(line, /c\.path\(/, `${f}: ${op} without c.path():\n    ${line.trim()}`);
      }
    }
  }
});

test('the operator picker is server-side: api/me refuses candidates and off-roster ids', () => {
  const src = code(read('api/me.js'));
  assert.match(src, /who\.role !== 'operator'/, 'a candidate must not be able to switch');
  assert.match(src, /roster\.candidates\.find\(\(c\) => c\.id === as\)/, 'the pick is checked against the roster');
});

test('every error code the callback can emit has a message on the login page', () => {
  const cb = code(read('api/auth/callback/google.js'));
  const start = code(read('api/auth/google.js'));
  const emitted = new Set();
  for (const m of (cb + start).matchAll(/back\(res,\s*'([a-z_]+)'/g)) emitted.add(m[1]);
  for (const m of start.matchAll(/login\?error=([a-z_]+)/g)) emitted.add(m[1]);
  assert.ok(emitted.size >= 6, `only found ${emitted.size} codes — the scan is probably broken`);

  const login = read('login.html');
  const table = login.slice(login.indexOf('var ERRORS = {'), login.indexOf('};', login.indexOf('var ERRORS = {')));
  const explained = new Set([...table.matchAll(/^\s*([a-z_]+):/gm)].map((m) => m[1]));
  for (const c of emitted) assert.ok(explained.has(c), `login.html has no message for error code "${c}"`);
  for (const c of explained) assert.ok(emitted.has(c), `login.html explains "${c}", which nothing emits`);
});

test('nothing candidate-specific ships as a static file', () => {
  const ignore = read('.vercelignore');
  for (const pat of ['resume*.pdf', 'resume.html', 'guide.html']) {
    assert.ok(ignore.split('\n').some((l) => l.trim() === pat), `.vercelignore is missing ${pat}`);
  }
  const deploy = read('deploy.sh');
  assert.match(deploy, /window\.__DESK_DATA = \[\];/, 'deploy.sh must always ship an empty dataset');
  assert.ok(!/cp \.\.\/review-app\/desk-data\.js/.test(deploy), 'deploy.sh still copies a real export into the bundle');
  assert.ok(!/cp \.\.\/review-app\/guide\.html/.test(deploy), 'deploy.sh still ships the guide with a real screenshot');
});

test('both front ends load data from the API, keyed by who signed in', () => {
  const desk = code(read('../review-app/The Desk - Triage.dc.html'));
  const m = code(read('m.html'));
  for (const [name, src] of [['desk', desk], ['m.html', m]]) {
    assert.match(src, /fetch\('api\/me'/, `${name} never asks who is signed in`);
    assert.match(src, /fetch\('api\/jobs'/, `${name} does not load jobs from the API`);
    assert.match(src, /api\/auth\/signout/, `${name} has no sign-out`);
    assert.match(src, /desk-status-v2:?'\s*\+\s*/, `${name} caches decisions in one shared localStorage key`);
  }
  assert.ok(!/<script src="\.\/desk-data\.js">/.test(m), 'm.html still boots from the static bundle');
  // The design preview's sample jobs must never show on a real deployment.
  assert.match(desk, /Array\.isArray\(window\.__DESK_DATA\)/, 'the Desk must treat an empty array as real, not as a cue for sample jobs');
});

test('the ops page is operator-only end to end', () => {
  const ops = code(read('ops.html'));
  assert.match(ops, /fetch\('api\/me'/, 'ops.html must check who is signed in before asking for usage');
  assert.match(ops, /me\.role !== 'operator'/, 'a candidate reaching /ops is sent to the Desk');
  assert.match(ops, /fetch\('api\/usage'/);
  assert.match(ops, /api\/auth\/signout/);
  const api = code(read('api/usage.js'));
  assert.match(api, /requireOperator\(req,\s*res\)/, 'api/usage must refuse candidates server-side, not just in the page');
  assert.ok(!/requireCandidate/.test(api), 'usage is not per-candidate data');
  // Never an empty-looking success on a read failure.
  assert.match(ops, /not a report of zero|nothing having been spent/, 'the page must distinguish "could not read" from "zero"');
});

test('the test directory itself is not deployed, and this suite is not empty', () => {
  assert.ok(read('.vercelignore').includes('tests/'));
  const files = readdirSync(new URL('.', import.meta.url)).filter((f) => f.endsWith('.test.mjs'));
  assert.ok(files.length >= 4, `expected at least 4 suites, found ${files.length}`);
});
