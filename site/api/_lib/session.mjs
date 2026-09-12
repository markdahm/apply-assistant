// Session cookies, signed with HMAC-SHA256.
//
// Ported from SolisCalendarEditor/src/lib/session.ts on 12 September 2026, where
// it has been in production behind Google sign-in since 23 August. Plain
// JavaScript here because this site has no build step: middleware.js imports
// it as ESM, and the CommonJS handlers in api/ reach it with a dynamic import.
//
// Web Crypto rather than node:crypto because middleware.js runs on the Edge
// runtime, where node:crypto does not exist. The same code then works in both
// places, which matters: a verifier that behaves differently in middleware than
// in a route handler is a gate with two different opinions about who is let in.
//
// The cookie CARRIES THE SIGNED-IN ADDRESS, and that is the point of it. The old
// Desk cookie was sha256(DESK_PASSWORD): identical for every visitor, carrying
// no identity, revocable only by rotating the password. Now the address rides
// inside the signature, so the gate can ask on every request whether this
// person is still on the roster, and a removal takes effect immediately rather
// than at the end of a thirty-day session.
//
// The address is signed, not encrypted. It is not a secret — it is the reader's
// own address, visible to them in their own cookie jar — and signing is what
// stops them editing it.

const encoder = new TextEncoder();
const decoder = new TextDecoder();

export const SESSION_COOKIE = 'desk_session';
export const SESSION_TTL_MS = 30 * 24 * 60 * 60 * 1000;   // 30 days

async function hmacKey(secret) {
  return crypto.subtle.importKey(
    'raw',
    encoder.encode(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign', 'verify'],
  );
}

function toHex(buf) {
  return Array.from(new Uint8Array(buf)).map((b) => b.toString(16).padStart(2, '0')).join('');
}

// base64url, because the payload contains an email address and addresses
// contain dots. The token is split on '.', so anything with a dot in it has to
// be encoded first or the split lands in the middle of the address.
export function b64urlEncode(s) {
  const bytes = encoder.encode(s);
  let bin = '';
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

export function b64urlDecode(s) {
  try {
    const b64 = s.replace(/-/g, '+').replace(/_/g, '/');
    const bin = atob(b64 + '='.repeat((4 - (b64.length % 4)) % 4));
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return decoder.decode(bytes);
  } catch {
    return null;
  }
}

// `<base64url(expiresAtMs|email)>.<hmac>`
//
// The expiry and the address are both in the clear and both signed, so neither
// can be edited. The signature covers the ENCODED payload — the exact bytes
// that travel — so there is no question of two strings canonicalising to one.
export async function signSession(secret, email, now = Date.now()) {
  const payload = b64urlEncode(`${now + SESSION_TTL_MS}|${String(email).trim().toLowerCase()}`);
  const sig = await crypto.subtle.sign('HMAC', await hmacKey(secret), encoder.encode(payload));
  return `${payload}.${toHex(sig)}`;
}

// Returns { email } or null. Callers must treat null as "no session" — there is
// deliberately no boolean form, because the address is the thing the gate needs
// in order to re-check the roster, and a boolean would let a caller skip that.
export async function verifySession(secret, token, now = Date.now()) {
  if (!secret || !token) return null;

  // Exactly one dot. Splitting on the first and ignoring the rest would let a
  // token carry trailing junk that the signature never covered.
  const parts = token.split('.');
  if (parts.length !== 2) return null;

  const [payload, sig] = parts;
  if (!payload) return null;

  const expected = toHex(await crypto.subtle.sign('HMAC', await hmacKey(secret), encoder.encode(payload)));
  if (!timingSafeEqual(sig, expected)) return null;

  // Only now is the payload worth reading: it has been proved to be ours.
  const decoded = b64urlDecode(payload);
  if (!decoded) return null;
  const cut = decoded.indexOf('|');
  if (cut < 0) return null;

  const expires = decoded.slice(0, cut);
  const email = decoded.slice(cut + 1);
  if (!/^\d+$/.test(expires)) return null;
  if (Number(expires) <= now) return null;
  if (!email) return null;

  return { email };
}

// Compare without leaking where two strings first differ. Over a network the
// margin is largely theoretical, but the constant-time version is no harder to
// write than the one that returns early.
export function timingSafeEqual(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

// Is this request running over HTTPS?
//
// On Vercel the function sees the proxy's connection, so the URL can say http
// while the browser is on https. `x-forwarded-proto` is the authority when
// present, and only the FIRST entry counts — a chain of proxies appends, so
// "https,http" still means the browser used https.
//
// `request` needs only `.headers.get(name)`; the Node handlers wrap their
// header object to match (see api/_who.js).
export function isSecureRequest(request, url) {
  const proto = request.headers.get('x-forwarded-proto');
  if (proto) return proto.split(',')[0].trim().toLowerCase() === 'https';
  return url.protocol === 'https:';
}

// Is this a safe place to send somebody after they sign in?
//
// Only a path on this site. The obvious guard — "starts with / but not //" — is
// not enough: several browsers normalise a BACKSLASH to a forward slash, so
// `/\evil.com` becomes `//evil.com` and the redirect leaves the site. Any
// backslash anywhere is refused, along with anything that looks like a scheme
// or an authority. Same rule the retired password login enforced.
export function safeNextPath(next) {
  if (!next) return '/';
  if (!next.startsWith('/')) return '/';
  if (next.startsWith('//')) return '/';
  if (next.includes('\\')) return '/';
  if (next.includes(':')) return '/';        // no javascript:, data:, http:
  if (next.includes('\n') || next.includes('\r')) return '/';
  return next;
}

// Serialise a Set-Cookie header. One place, so every cookie this site sets
// carries the same attributes — a browser matches a deletion to the cookie it
// replaces on name, path and Secure, so clearing one with a different shape
// leaves the original in place: a sign-out that signs nobody out.
export function cookieHeader(name, value, { maxAge, secure, httpOnly = true }) {
  const parts = [`${name}=${value}`, 'Path=/', 'SameSite=Lax'];
  if (httpOnly) parts.push('HttpOnly');
  if (secure) parts.push('Secure');
  if (maxAge !== undefined) parts.push(`Max-Age=${maxAge}`);
  return parts.join('; ');
}

// Read one cookie out of a raw Cookie header. Returns '' when absent.
export function readCookie(header, name) {
  const raw = String(header || '');
  for (const part of raw.split(';')) {
    const s = part.trim();
    if (s.startsWith(name + '=')) return s.slice(name.length + 1);
  }
  return '';
}
