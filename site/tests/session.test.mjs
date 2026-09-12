// Session cookies and the post-login redirect.
//
//   cd site && npm test        (or: pytest tests/test_site_js.py from the root)
//
// This is the check standing between the internet and a real person's resume,
// so it is tested against hostile input rather than the happy path. The
// redirect table is written out deliberately: a regex that looks obviously
// right handles the attack you thought of, not the one you did not.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  signSession, verifySession, timingSafeEqual, safeNextPath, isSecureRequest,
  b64urlEncode, cookieHeader, readCookie, SESSION_TTL_MS, SESSION_COOKIE,
} from '../api/_lib/session.mjs';

const SECRET = 'a-test-secret-value';
const WHO = 'someone@example.com';

test('a freshly signed session verifies, and says who it is for', async () => {
  const token = await signSession(SECRET, WHO);
  assert.deepEqual(await verifySession(SECRET, token), { email: WHO });
});

test('the address is normalised on the way in', async () => {
  const token = await signSession(SECRET, '  SomeOne@Example.COM  ');
  assert.deepEqual(await verifySession(SECRET, token), { email: WHO });
});

test('a session signed with a different secret does not verify', async () => {
  const token = await signSession('some-other-secret', WHO);
  assert.equal(await verifySession(SECRET, token), null);
});

test('an expired session does not', async () => {
  const issued = Date.now() - SESSION_TTL_MS - 1000;
  const token = await signSession(SECRET, WHO, issued);
  assert.equal(await verifySession(SECRET, token), null);
});

test('the expiry cannot be extended, because it is signed', async () => {
  const token = await signSession(SECRET, WHO);
  const [, sig] = token.split('.');
  const forged = `${b64urlEncode(`${Date.now() + 10 * SESSION_TTL_MS}|${WHO}`)}.${sig}`;
  assert.equal(await verifySession(SECRET, forged), null);
});

test('the ADDRESS cannot be swapped either — that would be the whole gate', async () => {
  // Without this, any candidate with a valid session could rewrite the payload
  // to the operator's address and see every other candidate's Desk.
  const token = await signSession(SECRET, WHO);
  const [payload, sig] = token.split('.');
  const decoded = Buffer.from(payload.replace(/-/g, '+').replace(/_/g, '/'), 'base64').toString();
  const expires = decoded.split('|')[0];
  const forged = `${b64urlEncode(`${expires}|operator@example.com`)}.${sig}`;
  assert.equal(await verifySession(SECRET, forged), null);
});

test('the retired password cookie is not a session', async () => {
  // The old Desk cookie was `desk=<sha256 of the password>`. It must not
  // verify under any secret — it carries no identity for the roster to check.
  const { createHash } = await import('node:crypto');
  const legacy = createHash('sha256').update('the-old-password').digest('hex');
  assert.equal(await verifySession(SECRET, legacy), null);
  assert.equal(await verifySession('the-old-password', legacy), null);
});

test('malformed tokens are refused', async () => {
  for (const bad of [
    undefined, '', 'nonsense', 'abc.def',
    '9999999999999',
    '9999999999999.',
    '.abc',
    '9999999999999.abc.def',
    'NaN.abc',
    '-1.abc',
    `${b64urlEncode('no-pipe-here')}.abc`,
    `${b64urlEncode('|someone@example.com')}.abc`,
    `${b64urlEncode('9999999999999|')}.abc`,
  ]) {
    assert.equal(await verifySession(SECRET, bad), null, `accepted ${JSON.stringify(bad)}`);
  }
});

test('no secret means no entry, rather than everything', async () => {
  const token = await signSession(SECRET, WHO);
  assert.equal(await verifySession('', token), null);
  assert.equal(await verifySession(undefined, token), null);
});

test('timingSafeEqual is still correct as well as constant-time', () => {
  assert.equal(timingSafeEqual('abc', 'abc'), true);
  assert.equal(timingSafeEqual('abc', 'abd'), false);
  assert.equal(timingSafeEqual('abc', 'abcd'), false);
  assert.equal(timingSafeEqual('', ''), true);
});

test('HTTPS is decided by the proxy header when there is one', () => {
  const httpUrl = new URL('http://internal.local/api/auth/google');
  const httpsUrl = new URL('https://job-desk-theta.vercel.app/api/auth/google');
  const req = (proto) => ({ headers: { get: (k) => (k === 'x-forwarded-proto' ? proto : null) } });

  assert.equal(isSecureRequest(req('https'), httpUrl), true, 'proxy said https; we said no');
  assert.equal(isSecureRequest(req('https,http'), httpUrl), true, 'only the FIRST hop counts');
  assert.equal(isSecureRequest(req('HTTPS'), httpUrl), true, 'header case must not matter');
  assert.equal(isSecureRequest(req('http'), httpsUrl), false, 'header outranks url.protocol');
  assert.equal(isSecureRequest(req(null), httpsUrl), true, 'no header: fall back to the URL');
  assert.equal(isSecureRequest(req(null), httpUrl), false, 'plain local http is not secure');
});

test('the post-login redirect only ever goes somewhere on this site', () => {
  for (const ok of ['/', '/onboard', '/desk?x=1', '/m#top']) {
    assert.equal(safeNextPath(ok), ok, `rejected a legitimate path: ${ok}`);
  }
  for (const bad of [
    '//evil.com', '/\\evil.com', '/\\\\evil.com', '\\\\evil.com',
    'https://evil.com', 'http://evil.com', '//evil.com/path',
    'javascript:alert(1)', 'data:text/html,<script>',
    '/redirect?to=https://evil.com',
    '/\nSet-Cookie: x=y', '/\r\nLocation: https://evil.com',
    'evil.com', '', null, undefined,
  ]) {
    assert.equal(safeNextPath(bad), '/', `allowed a hostile redirect: ${JSON.stringify(bad)}`);
  }
});

test('cookies are serialised with the same attributes every time', () => {
  const set = cookieHeader(SESSION_COOKIE, 'abc', { maxAge: 60, secure: true });
  assert.equal(set, 'desk_session=abc; Path=/; SameSite=Lax; HttpOnly; Secure; Max-Age=60');
  // A deletion has the same shape, so the browser matches it to the original.
  const del = cookieHeader(SESSION_COOKIE, '', { maxAge: 0, secure: true });
  assert.equal(del, 'desk_session=; Path=/; SameSite=Lax; HttpOnly; Secure; Max-Age=0');
  assert.ok(!cookieHeader('x', 'y', { secure: false }).includes('Secure'), 'local http must not set Secure');
});

test('readCookie finds one cookie among many and nothing else', () => {
  const jar = 'a=1; desk_session=tok.sig; desk_as=abcdef0123456789; b=2';
  assert.equal(readCookie(jar, 'desk_session'), 'tok.sig');
  assert.equal(readCookie(jar, 'desk_as'), 'abcdef0123456789');
  assert.equal(readCookie(jar, 'desk'), '', 'a prefix of a name is not the name');
  assert.equal(readCookie(jar, 'missing'), '');
  assert.equal(readCookie(undefined, 'desk_session'), '');
});
