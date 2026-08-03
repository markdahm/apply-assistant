// Edge gate: every path except the login flow requires the desk cookie
// (sha256 of DESK_PASSWORD).

import { next } from '@vercel/functions';

export const config = {
  matcher: '/((?!api/login|login|favicon.ico|_vercel).*)',
};

async function sha256hex(s) {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s));
  return Array.from(new Uint8Array(buf)).map((b) => b.toString(16).padStart(2, '0')).join('');
}

export default async function middleware(req) {
  const cookie = req.headers.get('cookie') || '';
  const m = cookie.match(/(?:^|;\s*)desk=([a-f0-9]{64})/);
  if (m) {
    const expected = await sha256hex(process.env.DESK_PASSWORD || '');
    if (m[1] === expected) return next();
  }
  return Response.redirect(new URL('/login', req.url), 302);
}
