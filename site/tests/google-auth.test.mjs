// Signing in with Google, and PKCE.
//
//   cd site && npm test
//
// Tested the way session.test.mjs is: against what an attacker would send.
// The value of PKCE rests on three things being true at once — the challenge
// really is S256 of the verifier, the verifier really travels to the exchange,
// and it is stored where the browser cannot read it. A verifier generated and
// then not sent is decoration, and nothing else would notice.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import {
  newState, newVerifier, challengeFor, googleAuthUrl, encodeAuthState, decodeAuthState,
  decodeIdToken, verifyIdTokenClaims, callbackUrlFor, GOOGLE_SCOPES,
} from '../api/_lib/google-auth.mjs';

const CLIENT = '123-abc.apps.googleusercontent.com';

const idToken = (claims) => {
  const b64 = (o) => Buffer.from(JSON.stringify(o)).toString('base64')
    .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return `${b64({ alg: 'RS256' })}.${b64(claims)}.notachecked_signature`;
};
const valid = (over = {}) => ({
  iss: 'https://accounts.google.com', aud: CLIENT,
  exp: Math.floor(Date.now() / 1000) + 3600,
  email: 'mark@example.com', email_verified: true, ...over,
});

// ── The authorize URL ───────────────────────────────────────────────────────

test('the authorize URL carries what Google needs and nothing more', async () => {
  const u = new URL(googleAuthUrl({ clientId: CLIENT, redirectUri: 'https://x.test/cb', state: 'abc', codeChallenge: 'chal' }));
  assert.equal(u.origin + u.pathname, 'https://accounts.google.com/o/oauth2/v2/auth');
  assert.equal(u.searchParams.get('client_id'), CLIENT);
  assert.equal(u.searchParams.get('redirect_uri'), 'https://x.test/cb');
  assert.equal(u.searchParams.get('response_type'), 'code');
  assert.equal(u.searchParams.get('state'), 'abc');
  assert.equal(u.searchParams.get('scope'), GOOGLE_SCOPES);
  assert.ok(!/gmail|drive|spreadsheets/.test(GOOGLE_SCOPES), 'identity only');
  assert.equal(u.searchParams.get('access_type'), 'online', 'no refresh token');
  assert.equal(u.searchParams.get('code_challenge'), 'chal');
  assert.equal(u.searchParams.get('code_challenge_method'), 'S256');
});

test('state is random and long enough to be worth checking', () => {
  const seen = new Set();
  for (let i = 0; i < 200; i++) seen.add(newState());
  assert.equal(seen.size, 200, 'state repeated');
  assert.ok(newState().length >= 32);
});

test('the callback URL is derived from the origin, so both environments are right', () => {
  assert.equal(callbackUrlFor('http://localhost:3000'), 'http://localhost:3000/api/auth/callback/google');
  assert.equal(callbackUrlFor('https://job-desk-theta.vercel.app'),
    'https://job-desk-theta.vercel.app/api/auth/callback/google');
});

// ── PKCE ─────────────────────────────────────────────────────────────────────

test('the verifier is long enough, random, and needs no escaping', () => {
  const a = newVerifier(); const b = newVerifier();
  assert.notEqual(a, b);
  assert.ok(a.length >= 43 && a.length <= 128, `verifier length ${a.length} out of range`);
  assert.match(a, /^[A-Za-z0-9\-._~]+$/);
});

test('the challenge is genuinely S256 of the verifier', async () => {
  // Computed independently with node:crypto — calling the helper twice would
  // prove only that it is deterministic.
  const verifier = newVerifier();
  const expected = createHash('sha256').update(verifier).digest('base64')
    .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  assert.equal(await challengeFor(verifier), expected);
  assert.notEqual(await challengeFor(verifier), verifier, 'S256, never plain');
});

test('the callback actually sends the verifier — otherwise PKCE is decoration', () => {
  const src = readFileSync(new URL('../api/auth/callback/google.js', import.meta.url), 'utf8');
  assert.match(src, /code_verifier: started\.verifier/);
});

test('the verifier is stored where the browser cannot read it', () => {
  const src = readFileSync(new URL('../api/auth/google.js', import.meta.url), 'utf8');
  assert.match(src, /cookieHeader\(\s*OAUTH_STATE_COOKIE/, 'state cookie is set through the shared helper (HttpOnly by default)');
  assert.ok(!/httpOnly:\s*false/.test(src), 'the state cookie must be HttpOnly');
  assert.ok(!/localStorage|sessionStorage/.test(src), 'the verifier is in browser storage');
});

test('the auth-state cookie round-trips all three values', () => {
  const v = { state: 'abc123', verifier: 'v-e-r', next: '/a/b?c=d#e' };
  assert.deepEqual(decodeAuthState(encodeAuthState(v)), v);
  for (const next of ['/a|b', '/a.b|c', '/x?q=1|2', '/plain']) {
    assert.equal(decodeAuthState(encodeAuthState({ state: 's', verifier: 'v', next })).next, next, `mangled ${next}`);
  }
});

test('a junk state cookie is refused rather than half-read', () => {
  for (const bad of [
    undefined, '', 'not-base64!!', 'YWJj',
    Buffer.from('{"state":"s"}').toString('base64url'),
    Buffer.from('{"verifier":"v"}').toString('base64url'),
    Buffer.from('[]').toString('base64url'),
    Buffer.from('null').toString('base64url'),
  ]) {
    assert.equal(decodeAuthState(bad), null, `accepted ${JSON.stringify(bad)}`);
  }
});

// ── The ID token ─────────────────────────────────────────────────────────────

test('a well-formed token decodes; anything malformed decodes to null', () => {
  assert.equal(decodeIdToken(idToken(valid())).email, 'mark@example.com');
  for (const bad of [undefined, '', 'a', 'a.b', 'a.b.c.d', 'a.!!!.c', '..']) {
    assert.equal(decodeIdToken(bad), null, `did not reject ${JSON.stringify(bad)}`);
  }
});

test('a token minted for another app is refused', () => {
  const r = verifyIdTokenClaims(valid({ aud: 'someone-else.apps.googleusercontent.com' }), CLIENT);
  assert.equal(r.ok, false);
  assert.match(r.reason, /another app/);
});

test('expired, wrong issuer, unverified email, no client id — all refused', () => {
  assert.match(verifyIdTokenClaims(valid({ exp: Math.floor(Date.now() / 1000) - 1 }), CLIENT).reason, /expired/);
  assert.match(verifyIdTokenClaims(valid({ iss: 'https://evil.example' }), CLIENT).reason, /did not come from Google/);
  assert.match(verifyIdTokenClaims(valid({ email_verified: false }), CLIENT).reason, /verified email/);
  assert.match(verifyIdTokenClaims(valid(), '').reason, /not configured/);
  assert.equal(verifyIdTokenClaims(null, CLIENT).ok, false);
});

test('both issuer spellings and the string "true" for email_verified are accepted', () => {
  for (const iss of ['accounts.google.com', 'https://accounts.google.com']) {
    assert.equal(verifyIdTokenClaims(valid({ iss }), CLIENT).ok, true, iss);
  }
  assert.equal(verifyIdTokenClaims(valid({ email_verified: 'true' }), CLIENT).ok, true);
});

test('a valid token yields the email and nothing else is trusted from it', () => {
  const r = verifyIdTokenClaims(valid({ email: '  Mark@Example.com  ' }), CLIENT);
  assert.equal(r.ok, true);
  assert.equal(r.email, 'Mark@Example.com');
});
