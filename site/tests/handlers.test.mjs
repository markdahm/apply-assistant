// The Node handlers, driven end to end with fake requests.
//
//   cd site && npm test
//
// roster.test.mjs proves the decision logic; this file proves the HANDLERS
// actually consult it and act on the answer — a handler that forgot to call
// requireCandidate() passes every roster test and leaks every candidate's
// data. It also runs the whole sign-in: start → (Google, stubbed) → callback →
// a session cookie that the gate then accepts. No network, no Vercel: the two
// packages the handlers import are replaced with recording stubs, and fetch()
// is replaced per test.

import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import Module from 'node:module';
import { decide, AS_COOKIE } from '../api/_lib/who.mjs';
import { candidateId } from '../api/_lib/roster.mjs';
import { signSession, SESSION_COOKIE } from '../api/_lib/session.mjs';

// ── Stub the two Vercel packages the handlers require ───────────────────────
const blobCalls = [];
const blobStub = {
  put: async (pathname, body, opts) => { blobCalls.push({ op: 'put', pathname, body, opts }); return { url: 'https://x/' + pathname }; },
  list: async ({ prefix }) => { blobCalls.push({ op: 'list', prefix }); return { blobs: listing.filter((b) => b.pathname.startsWith(prefix)) }; },
};
let listing = [];
const realLoad = Module._load;
Module._load = function (request, parent, isMain) {
  if (request === '@vercel/blob') return blobStub;
  return realLoad.call(this, request, parent, isMain);
};
const require = createRequire(import.meta.url);
const H = {
  jobs: require('../api/jobs.js'),
  status: require('../api/status.js'),
  inbox: require('../api/inbox.js'),
  letter: require('../api/letter.js'),
  onboard: require('../api/onboard.js'),
  me: require('../api/me.js'),
  start: require('../api/auth/google.js'),
  callback: require('../api/auth/callback/google.js'),
  signout: require('../api/auth/signout.js'),
  usage: require('../api/usage.js'),
  config: require('../api/config.js'),
};

// ── Fake req/res ────────────────────────────────────────────────────────────
function req({ method = 'GET', url = '/api/x', cookie = '', body = null, headers = {} } = {}) {
  const h = { host: 'desk.test', 'x-forwarded-proto': 'https', ...headers };
  if (cookie) h.cookie = cookie;
  const listeners = {};
  const r = {
    method, url, headers: h,
    // Deliver the body once the handler has attached its 'end' listener. The
    // handlers await requireCandidate() BEFORE calling readBody(), so a timer
    // armed at construction fires into the void and readBody never resolves —
    // the first version of this helper hung the whole suite that way.
    on(ev, fn) {
      listeners[ev] = fn;
      if (ev === 'end') {
        setTimeout(() => {
          if (body != null && listeners.data) listeners.data(typeof body === 'string' ? body : JSON.stringify(body));
          listeners.end();
        }, 0);
      }
      return r;
    },
    destroy() {},
  };
  return r;
}
function res() {
  const r = { statusCode: 200, headers: {}, body: '', ended: false };
  r.setHeader = (k, v) => { r.headers[k.toLowerCase()] = v; };
  r.end = (b) => { r.body = b == null ? '' : String(b); r.ended = true; return r; };
  r.json = () => JSON.parse(r.body || 'null');
  r.cookies = () => [].concat(r.headers['set-cookie'] || []);
  return r;
}
async function call(handler, options) { const rs = res(); await handler(req(options), rs); return rs; }

const ENV = {
  DESK_OPERATORS: 'mark@example.com',
  DESK_CANDIDATES: 'ann@example.com, bea@example.com',
  DESK_SESSION_SECRET: 'handler-test-secret',
  DESK_GOOGLE_CLIENT_ID: 'cid.apps.googleusercontent.com',
  DESK_GOOGLE_CLIENT_SECRET: 'csecret',
  BLOB_STORE_ID: 'store_ABC',
  BLOB_READ_WRITE_TOKEN: 'tok',
};
const cookieFor = async (email, as) => {
  let c = `${SESSION_COOKIE}=${await signSession(ENV.DESK_SESSION_SECRET, email)}`;
  if (as) c += `; ${AS_COOKIE}=${as}`;
  return c;
};
let ANN, BEA;
const realFetch = globalThis.fetch;

beforeEach(async () => {
  Object.assign(process.env, ENV);
  blobCalls.length = 0;
  listing = [];
  ANN = await candidateId('ann@example.com');
  BEA = await candidateId('bea@example.com');
  // Default fetch: every derived blob URL is "not published yet".
  globalThis.fetch = async () => new Response('', { status: 404 });
});

// ── Refusals ────────────────────────────────────────────────────────────────

test('every data handler answers 401 with no session, before touching storage', async () => {
  for (const [name, h] of Object.entries({ jobs: H.jobs, status: H.status, inbox: H.inbox, letter: H.letter, onboard: H.onboard, me: H.me })) {
    const r = await call(h, {});
    assert.equal(r.statusCode, 401, name);
    assert.equal(r.json().ok, false, name);
  }
  assert.deepEqual(blobCalls, [], 'storage was touched by an unauthenticated request');
});

test('an operator who has not picked a candidate gets 409, not an empty queue', async () => {
  const r = await call(H.jobs, { cookie: await cookieFor('mark@example.com') });
  assert.equal(r.statusCode, 409);
  assert.match(r.body, /no candidate selected/);
  assert.deepEqual(blobCalls, []);
});

// ── Whose data ──────────────────────────────────────────────────────────────

test('api/jobs reads the signed-in candidate\'s own prefix and nobody else\'s', async () => {
  const seen = [];
  globalThis.fetch = async (url) => { seen.push(String(url)); return new Response('{"data":[1]}', { status: 200 }); };
  const r = await call(H.jobs, { cookie: await cookieFor('ann@example.com', BEA) });   // forged desk_as
  assert.equal(r.statusCode, 200);
  assert.equal(seen.length, 1);
  assert.match(seen[0], new RegExp(`^https://abc\\.private\\.blob\\.vercel-storage\\.com/c/${ANN}/desk-data-live\\.json`));
  assert.ok(!seen[0].includes(BEA), 'a candidate with a forged desk_as read another candidate\'s data');
});

test('an operator\'s pick decides which prefix api/jobs reads', async () => {
  const seen = [];
  globalThis.fetch = async (url) => { seen.push(String(url)); return new Response('{}', { status: 200 }); };
  await call(H.jobs, { cookie: await cookieFor('mark@example.com', BEA) });
  assert.match(seen[0], new RegExp(`/c/${BEA}/desk-data-live\\.json`));
});

test('api/status writes decisions under the candidate\'s prefix, cleaned', async () => {
  const r = await call(H.status, {
    method: 'POST', cookie: await cookieFor('ann@example.com'),
    body: { j1: { status: 'Applied', decidedAt: 5 }, j2: { status: 'Bogus' }, j3: { cover: 'x' } },
  });
  assert.equal(r.statusCode, 200);
  const put = blobCalls.find((c) => c.op === 'put');
  assert.equal(put.pathname, `c/${ANN}/status.json`);
  assert.deepEqual(JSON.parse(put.body), { j1: { status: 'Applied', decidedAt: 5 }, j3: { cover: 'x' } });
  assert.equal(put.opts.access, 'private');
  assert.equal(put.opts.addRandomSuffix, false);
});

test('api/inbox and api/letter queue under the candidate\'s prefix and list only it', async () => {
  const cookie = await cookieFor('bea@example.com');
  await call(H.inbox, { method: 'POST', cookie, body: { links: ['https://jobs.example/1'] } });
  await call(H.letter, { method: 'POST', cookie, body: { uid: 'abcdef1234' } });
  const puts = blobCalls.filter((c) => c.op === 'put').map((c) => c.pathname);
  assert.equal(puts.length, 2);
  for (const p of puts) assert.ok(p.startsWith(`c/${BEA}/`), p);
  assert.match(puts[0], /\/inbox\/[a-z0-9]+\.json$/);
  assert.match(puts[1], /\/letter-requests\/[a-z0-9]+\.json$/);

  blobCalls.length = 0;
  await call(H.inbox, { cookie });
  await call(H.letter, { cookie });
  assert.deepEqual(blobCalls.map((c) => c.prefix), [`c/${BEA}/inbox/`, `c/${BEA}/letter-requests/`]);
});

test('api/onboard files a submission under the candidate and records who submitted it', async () => {
  // The operator fills the form in on the candidate's behalf: the blob goes under
  // ANN's prefix, and the envelope says mark submitted it.
  const r = await call(H.onboard, {
    method: 'POST', cookie: await cookieFor('mark@example.com', ANN),
    body: { name: 'Ann', email: 'typed@elsewhere.example', resume: 'r' },
  });
  assert.equal(r.statusCode, 200, r.body);
  const put = blobCalls.find((c) => c.op === 'put');
  assert.match(put.pathname, new RegExp(`^c/${ANN}/onboard/[a-z0-9]+\\.json$`));
  const env = JSON.parse(put.body);
  assert.equal(env.account, 'mark@example.com');
  assert.equal(env.candidate, 'ann@example.com');
  assert.equal(env.payload.email, 'typed@elsewhere.example', 'the typed address is kept for the resume; identity came from the session');
});

test('api/onboard prefill lists only this candidate\'s submissions', async () => {
  listing = [
    { pathname: `c/${ANN}/onboard/a.json`, url: 'https://x/a', uploadedAt: '2026-09-01T00:00:00Z' },
    { pathname: `c/${BEA}/onboard/b.json`, url: 'https://x/b', uploadedAt: '2026-09-02T00:00:00Z' },
  ];
  globalThis.fetch = async (url) => new Response(JSON.stringify({ payload: { name: String(url).endsWith('/a') ? 'Ann' : 'Bea' }, submittedAt: 1 }));
  const r = await call(H.onboard, { url: '/api/onboard?include=payload', cookie: await cookieFor('ann@example.com') });
  assert.equal(r.json().count, 1);
  assert.equal(r.json().payload.name, 'Ann');
});

// ── api/me ──────────────────────────────────────────────────────────────────

test('api/me tells a candidate who they are and nothing about anyone else', async () => {
  const r = await call(H.me, { cookie: await cookieFor('ann@example.com') });
  const me = r.json();
  assert.equal(me.role, 'candidate');
  assert.equal(me.candidate.email, 'ann@example.com');
  assert.equal(me.candidates, undefined, 'a candidate must not receive the roster');
});

test('api/me lets an operator pick a roster candidate, and refuses everyone else', async () => {
  const op = await cookieFor('mark@example.com');
  const r0 = await call(H.me, { cookie: op });
  assert.equal(r0.json().candidate, null);
  assert.equal(r0.json().candidates.length, 2);

  const r1 = await call(H.me, { method: 'POST', cookie: op, body: { as: BEA } });
  assert.equal(r1.statusCode, 200);
  assert.match(r1.cookies()[0], new RegExp(`^${AS_COOKIE}=${BEA}; Path=/; SameSite=Lax; HttpOnly; Secure; Max-Age=`));

  const r2 = await call(H.me, { method: 'POST', cookie: op, body: { as: 'ffffffffffffffff' } });
  assert.equal(r2.statusCode, 400);

  const r3 = await call(H.me, { method: 'POST', cookie: await cookieFor('ann@example.com'), body: { as: BEA } });
  assert.equal(r3.statusCode, 403, 'a candidate must not be able to switch');
});

// ── The whole sign-in, with Google stubbed ──────────────────────────────────

test('start → callback mints a session the gate accepts; the state cookie is consumed', async () => {
  const start = await call(H.start, { url: '/api/auth/google?next=/onboard' });
  assert.equal(start.statusCode, 302);
  const to = new URL(start.headers.location);
  assert.equal(to.origin + to.pathname, 'https://accounts.google.com/o/oauth2/v2/auth');
  assert.equal(to.searchParams.get('redirect_uri'), 'https://desk.test/api/auth/callback/google');
  assert.equal(to.searchParams.get('code_challenge_method'), 'S256');
  const stateCookie = start.cookies()[0];
  assert.match(stateCookie, /^desk_oauth_state=.+; Path=\/; SameSite=Lax; HttpOnly; Secure; Max-Age=600$/);
  const stateVal = stateCookie.split(';')[0].split('=')[1];

  // Google's token endpoint, stubbed: it must receive the PKCE verifier, and
  // it answers with an ID token for ann.
  let exchange = null;
  globalThis.fetch = async (url, init) => {
    exchange = { url: String(url), body: Object.fromEntries(new URLSearchParams(init.body)) };
    const b64 = (o) => Buffer.from(JSON.stringify(o)).toString('base64url');
    const id_token = `${b64({ alg: 'RS256' })}.${b64({ iss: 'https://accounts.google.com', aud: ENV.DESK_GOOGLE_CLIENT_ID, exp: Math.floor(Date.now() / 1000) + 60, email: 'ann@example.com', email_verified: true })}.sig`;
    return new Response(JSON.stringify({ id_token }), { status: 200 });
  };
  const cb = await call(H.callback, {
    url: `/api/auth/callback/google?code=thecode&state=${to.searchParams.get('state')}`,
    cookie: `desk_oauth_state=${stateVal}`,
  });
  assert.equal(cb.statusCode, 302, cb.body);
  assert.equal(cb.headers.location, '/onboard', 'the post-login destination survived the round trip');
  assert.equal(exchange.url, 'https://oauth2.googleapis.com/token');
  assert.equal(exchange.body.code, 'thecode');
  assert.ok(exchange.body.code_verifier && exchange.body.code_verifier.length >= 43, 'PKCE verifier was not sent');
  assert.equal(exchange.body.redirect_uri, 'https://desk.test/api/auth/callback/google');

  const set = cb.cookies();
  const session = set.find((c) => c.startsWith(SESSION_COOKIE + '='));
  const cleared = set.find((c) => c.startsWith('desk_oauth_state=;'));
  assert.ok(session, 'no session cookie set');
  assert.ok(cleared && /Max-Age=0/.test(cleared), 'the state cookie was not cleared');

  // And the gate accepts what the callback minted.
  const d = await decide({ pathname: '/desk', cookieHeader: session.split(';')[0], env: ENV });
  assert.deepEqual(d, { action: 'pass' });
});

test('the callback refuses a mismatched state, a stranger, and a token for another app', async () => {
  const start = await call(H.start, { url: '/api/auth/google' });
  const stateVal = start.cookies()[0].split(';')[0].split('=')[1];
  const state = new URL(start.headers.location).searchParams.get('state');
  const b64 = (o) => Buffer.from(JSON.stringify(o)).toString('base64url');
  const tokenFor = (claims) => `${b64({ alg: 'RS256' })}.${b64({ iss: 'https://accounts.google.com', aud: ENV.DESK_GOOGLE_CLIENT_ID, exp: Math.floor(Date.now() / 1000) + 60, email_verified: true, ...claims })}.sig`;

  const bad = await call(H.callback, { url: `/api/auth/callback/google?code=c&state=wrong`, cookie: `desk_oauth_state=${stateVal}` });
  assert.equal(bad.headers.location, '/login?error=bad_state');

  globalThis.fetch = async () => new Response(JSON.stringify({ id_token: tokenFor({ email: 'stranger@example.com' }) }));
  const stranger = await call(H.callback, { url: `/api/auth/callback/google?code=c&state=${state}`, cookie: `desk_oauth_state=${stateVal}` });
  assert.equal(stranger.headers.location, '/login?error=not_allowed');
  assert.ok(!stranger.cookies().some((c) => c.startsWith(SESSION_COOKIE + '=')), 'a stranger got a session cookie');

  globalThis.fetch = async () => new Response(JSON.stringify({ id_token: tokenFor({ email: 'ann@example.com', aud: 'other-app' }) }));
  const other = await call(H.callback, { url: `/api/auth/callback/google?code=c&state=${state}`, cookie: `desk_oauth_state=${stateVal}` });
  assert.equal(other.headers.location, '/login?error=bad_token');
});

test('the callback refuses everyone when the deployment is not configured', async () => {
  process.env.DESK_CANDIDATES = '';
  process.env.DESK_OPERATORS = '';
  const r = await call(H.callback, { url: '/api/auth/callback/google?code=c&state=s' });
  assert.equal(r.headers.location, '/login?error=not_configured');
});

// ── api/usage: operators only ───────────────────────────────────────────────

test('api/usage refuses a candidate with 403 and never touches storage', async () => {
  const r = await call(H.usage, { cookie: await cookieFor('ann@example.com') });
  assert.equal(r.statusCode, 403);
  assert.match(r.body, /operators only/);
  assert.deepEqual(blobCalls, []);
  assert.equal((await call(H.usage, {})).statusCode, 401);
});

test('api/usage lists the ops prefix for an operator and names each file\'s candidate', async () => {
  listing = [
    { pathname: `ops/usage/${ANN}.json`, url: 'https://x/ann', uploadedAt: '2026-09-12T00:00:00Z' },
    { pathname: 'ops/usage/ffffffffffffffff.json', url: 'https://x/other', uploadedAt: '2026-09-11T00:00:00Z' },
  ];
  globalThis.fetch = async (url) => new Response(JSON.stringify({ schema: 'usage-rollup/1', generatedAt: 'x', from: String(url) }));
  const r = await call(H.usage, { cookie: await cookieFor('mark@example.com') });
  assert.equal(r.statusCode, 200, r.body);
  assert.deepEqual(blobCalls.map((c) => c.prefix), ['ops/usage/']);
  const body = r.json();
  assert.equal(body.files.length, 2);
  assert.equal(body.files[0].candidateEmail, 'ann@example.com');
  assert.equal(body.files[1].candidateEmail, null, 'an id not on the roster is shown by id, not invented');
  assert.equal(body.files[0].rollup.schema, 'usage-rollup/1');
});

test('api/usage reports a file it could not read instead of dropping it', async () => {
  listing = [{ pathname: `ops/usage/${ANN}.json`, url: 'https://x/ann', uploadedAt: 'z' }];
  globalThis.fetch = async () => new Response('', { status: 403 });
  const body = (await call(H.usage, { cookie: await cookieFor('mark@example.com') })).json();
  assert.equal(body.files.length, 1);
  assert.equal(body.files[0].error, 'HTTP 403');
  assert.equal(body.files[0].rollup, undefined);
});

// ── api/config: the five source files, per candidate ────────────────────────

test('api/config GET reads the five files under the signed-in candidate\'s prefix only', async () => {
  const seen = [];
  globalThis.fetch = async (url) => {
    seen.push(String(url));
    if (String(url).includes('/config/resume.md')) return new Response('# resume', { status: 200 });
    return new Response('', { status: 404 });
  };
  const r = await call(H.config, { cookie: await cookieFor('ann@example.com', BEA) });   // forged desk_as again
  assert.equal(r.statusCode, 200, r.body);
  const body = r.json();
  assert.equal(body.candidate, 'ann@example.com');
  assert.deepEqual(body.files.map((f) => f.name), ['profile.json', 'sources.json', 'resume.md', 'experience_bank.md', 'voice_real.md']);
  assert.equal(body.files[2].content, '# resume');
  assert.equal(body.files[0].content, null, 'a file not in the store is null, not empty text');
  for (const u of seen) assert.ok(u.includes(`/c/${ANN}/config/`), u);
  assert.ok(!seen.some((u) => u.includes(BEA)), 'read another candidate\'s file');
});

test('api/config PUT stores a valid file under the candidate and records who saved it', async () => {
  const r = await call(H.config, {
    method: 'PUT', cookie: await cookieFor('mark@example.com', BEA),
    body: { name: 'profile.json', content: JSON.stringify({ candidate: { name: 'Bea' }, preferences: {} }) },
  });
  assert.equal(r.statusCode, 200, r.body);
  const puts = blobCalls.filter((c) => c.op === 'put');
  assert.deepEqual(puts.map((p) => p.pathname), [`c/${BEA}/config/profile.json`, `c/${BEA}/config/_meta.json`]);
  assert.equal(JSON.parse(puts[1].body)['profile.json'].by, 'mark@example.com', 'the operator, not the candidate, saved it');
  assert.equal(puts[0].opts.access, 'private');
});

test('api/config PUT refuses bad JSON, a profile missing its sections, an unknown name, and non-string content', async () => {
  const cookie = await cookieFor('ann@example.com');
  const cases = [
    [{ name: 'profile.json', content: '{oops' }, /not valid JSON/],
    [{ name: 'profile.json', content: '{"candidate": {}}' }, /preferences.*object/],   // quotes are JSON-escaped in the body
    [{ name: 'sources.json', content: '[1,2]' }, /JSON object/],
    [{ name: '../status.json', content: '{}' }, /not a config file/],
    [{ name: 'resume.md', content: 42 }, /content must be a string/],
  ];
  for (const [body, re] of cases) {
    const r = await call(H.config, { method: 'PUT', cookie, body });
    assert.equal(r.statusCode, 400, JSON.stringify(body));
    assert.match(r.body, re);
  }
  assert.deepEqual(blobCalls.filter((c) => c.op === 'put'), [], 'nothing invalid reached the store');
});

test('api/config refuses the unauthenticated and an operator with nobody picked', async () => {
  assert.equal((await call(H.config, {})).statusCode, 401);
  assert.equal((await call(H.config, { cookie: await cookieFor('mark@example.com') })).statusCode, 409);
});

// ── Where a sign-in lands ───────────────────────────────────────────────────

async function signInAs(email, next) {
  const start = await call(H.start, { url: '/api/auth/google' + (next ? '?next=' + encodeURIComponent(next) : '') });
  const stateVal = start.cookies()[0].split(';')[0].split('=')[1];
  const state = new URL(start.headers.location).searchParams.get('state');
  const b64 = (o) => Buffer.from(JSON.stringify(o)).toString('base64url');
  globalThis.fetch = async () => new Response(JSON.stringify({ id_token: `${b64({ alg: 'RS256' })}.${b64({ iss: 'https://accounts.google.com', aud: ENV.DESK_GOOGLE_CLIENT_ID, exp: Math.floor(Date.now() / 1000) + 60, email, email_verified: true })}.sig` }));
  return call(H.callback, { url: `/api/auth/callback/google?code=c&state=${state}`, cookie: `desk_oauth_state=${stateVal}` });
}

test('an operator with no destination lands on the ops page; a candidate lands on the Desk', async () => {
  assert.equal((await signInAs('mark@example.com')).headers.location, '/ops');
  assert.equal((await signInAs('ann@example.com')).headers.location, '/');
});

test('a specific destination wins over the ops page for everyone', async () => {
  assert.equal((await signInAs('mark@example.com', '/onboard')).headers.location, '/onboard');
  assert.equal((await signInAs('ann@example.com', '/m')).headers.location, '/m');
});

test('sign-out clears both cookies with the shape they were set with, and is POST only', async () => {
  const r = await call(H.signout, { method: 'POST', cookie: await cookieFor('ann@example.com', ANN) });
  assert.equal(r.statusCode, 200);
  assert.deepEqual(r.cookies().sort(), [
    `${AS_COOKIE}=; Path=/; SameSite=Lax; HttpOnly; Secure; Max-Age=0`,
    `${SESSION_COOKIE}=; Path=/; SameSite=Lax; HttpOnly; Secure; Max-Age=0`,
  ]);
  assert.equal((await call(H.signout, { method: 'GET' })).statusCode, 405);
});

test.after(() => { globalThis.fetch = realFetch; Module._load = realLoad; });
