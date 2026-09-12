// The gate. Everything is behind it except the sign-in flow itself.
//
// Runs on the Edge runtime, which is why the session verifier uses Web Crypto
// rather than node:crypto. This file is only the adapter between Vercel's
// request/response and decide() in api/_lib/who.mjs — the decision itself lives
// there, with no Vercel dependency, so `node --test` can drive it with real
// signed cookies. Keep this file free of logic; anything added here is
// something the tests cannot see.

import { next } from '@vercel/functions';
import { decide } from './api/_lib/who.mjs';

export const config = {
  // Vercel's own paths and the favicon carry nothing and are needed for the
  // login page itself to render.
  matcher: '/((?!favicon.ico|_vercel).*)',
};

export default async function middleware(req) {
  const url = new URL(req.url);
  const d = await decide({
    pathname: url.pathname,
    search: url.search,
    cookieHeader: req.headers.get('cookie') || '',
    env: process.env,
  });
  if (d.action === 'pass') return next();
  if (d.action === 'unauthorized') {
    return new Response('{"ok":false,"error":"not signed in"}', {
      status: 401, headers: { 'Content-Type': 'application/json' },
    });
  }
  return Response.redirect(new URL(d.to, req.url), 302);
}
