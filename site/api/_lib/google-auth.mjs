// Signing in with Google.
//
// Ported from SolisCalendarEditor/src/lib/google-auth.ts on 12 September 2026.
// Hand-rolled rather than a framework, on purpose: this site is static HTML plus
// a handful of Node functions, with no build step, and the whole flow is two
// Google endpoints and a signed cookie. Everything in this file is pure so it
// can be tested without a network; the handlers in api/auth/ are the only part
// that talks to Google.
//
// The ID token's signature is NOT verified here, and that is correct rather
// than lazy: it is received directly from Google's token endpoint over TLS, in a
// server-to-server exchange authenticated by the client secret. Google
// documents that case as not requiring local verification. The claims ARE
// checked, because they are free and they catch a token minted for somebody
// else's app.

import { b64urlEncode, b64urlDecode } from './session.mjs';

export const OAUTH_STATE_COOKIE = 'desk_oauth_state';
export const GOOGLE_AUTH_ENDPOINT = 'https://accounts.google.com/o/oauth2/v2/auth';
export const GOOGLE_TOKEN_ENDPOINT = 'https://oauth2.googleapis.com/token';

// Only identity. No Gmail, no Drive — nothing is done on the person's behalf.
export const GOOGLE_SCOPES = 'openid email profile';

// A random, URL-safe value tying a callback back to the request that started it.
export function newState() {
  const bytes = new Uint8Array(24);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
}

// PKCE.
//
// Not strictly required for a confidential client — the token exchange is
// already authenticated by the client secret — but it closes authorization-code
// injection independently of everything else. If a code ever leaks (a referer
// header, a proxy log, a shared machine's history), the code alone is useless
// without the verifier, which never leaves this server's cookie.
//
// 64 random bytes as base64url is 86 characters, inside RFC 7636's 43-128, and
// base64url is entirely unreserved characters so it needs no further escaping.
export function newVerifier() {
  const bytes = new Uint8Array(64);
  crypto.getRandomValues(bytes);
  let bin = '';
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

// S256, never `plain`. A plain challenge is the verifier, which defeats the point.
export async function challengeFor(verifier) {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier));
  let bin = '';
  for (const b of new Uint8Array(digest)) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

// The three things the callback needs, in one cookie.
//
// JSON inside base64url rather than a delimited string: `next` is a path and
// paths can contain very nearly anything, so any separator is a separator the
// destination might also contain.
export function encodeAuthState(v) {
  return b64urlEncode(JSON.stringify(v));
}

export function decodeAuthState(raw) {
  const json = b64urlDecode(String(raw ?? ''));
  if (!json) return null;
  try {
    const o = JSON.parse(json);
    if (!o || typeof o !== 'object') return null;
    const { state, verifier, next } = o;
    if (typeof state !== 'string' || !state) return null;
    if (typeof verifier !== 'string' || !verifier) return null;
    return { state, verifier, next: typeof next === 'string' ? next : '/' };
  } catch {
    return null;
  }
}

export function googleAuthUrl({ clientId, redirectUri, state, codeChallenge }) {
  const u = new URL(GOOGLE_AUTH_ENDPOINT);
  u.searchParams.set('client_id', clientId);
  u.searchParams.set('redirect_uri', redirectUri);
  u.searchParams.set('response_type', 'code');
  u.searchParams.set('scope', GOOGLE_SCOPES);
  u.searchParams.set('state', state);
  u.searchParams.set('code_challenge', codeChallenge);
  u.searchParams.set('code_challenge_method', 'S256');
  // No refresh token wanted: this is a sign-in, not an ongoing grant.
  u.searchParams.set('access_type', 'online');
  // Skip the chooser when there is one obvious account, but allow switching.
  u.searchParams.set('prompt', 'select_account');
  return u.toString();
}

// Read a JWT's payload. Signature ignored — see the note at the top. Returns
// null rather than throwing on anything malformed, so a mangled token is a
// failed sign-in rather than a 500.
export function decodeIdToken(jwt) {
  const parts = String(jwt ?? '').split('.');
  if (parts.length !== 3) return null;
  try {
    const b64 = parts[1].replace(/-/g, '+').replace(/_/g, '/');
    const pad = b64 + '='.repeat((4 - (b64.length % 4)) % 4);
    const json = typeof atob === 'function'
      ? atob(pad)
      : Buffer.from(pad, 'base64').toString('binary');
    const out = JSON.parse(decodeURIComponent(escape(json)));
    return out && typeof out === 'object' ? out : null;
  } catch {
    return null;
  }
}

// The claims worth checking on a token we already trust the delivery of.
//
// `aud` is the important one: it is what stops a token minted for a different
// application being replayed here. `exp` and `iss` are cheap. `email_verified`
// matters because an unverified address is not proof of anything.
export function verifyIdTokenClaims(claims, clientId, now = Date.now()) {
  if (!claims) return { ok: false, reason: 'the sign-in token could not be read' };
  if (!clientId) return { ok: false, reason: 'sign-in is not configured on this deployment' };
  if (claims.aud !== clientId) return { ok: false, reason: 'the sign-in token was issued for another app' };

  const iss = String(claims.iss ?? '');
  if (iss !== 'accounts.google.com' && iss !== 'https://accounts.google.com') {
    return { ok: false, reason: 'the sign-in token did not come from Google' };
  }
  if (typeof claims.exp !== 'number' || claims.exp * 1000 <= now) {
    return { ok: false, reason: 'the sign-in token has expired' };
  }
  const verified = claims.email_verified === true || claims.email_verified === 'true';
  if (!verified) return { ok: false, reason: 'that Google account has no verified email address' };

  const email = String(claims.email ?? '').trim();
  if (!email) return { ok: false, reason: 'that Google account gave no email address' };
  return { ok: true, email };
}

// The redirect URI this deployment registered with Google.
//
// Derived from the request's own origin rather than configured, so localhost
// and production each send the URI they will actually be called back on. A
// mismatch here is the single most common OAuth failure, and hardcoding one of
// the two guarantees it in the other.
export function callbackUrlFor(origin) {
  return new URL('/api/auth/callback/google', origin).toString();
}
