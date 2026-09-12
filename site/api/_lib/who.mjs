// Who is making this request, whose data may they see, and does the gate open.
//
// One module, used by middleware.js (Edge) and by every data handler in api/
// (Node, via api/_who.js), so there is exactly one opinion about identity. Two
// questions are asked, not one: did we issue this session, and is this person
// still on the roster? The roster is re-read from the environment on every call,
// so removing an address from DESK_OPERATORS or DESK_CANDIDATES locks that
// person out at once rather than at the end of a thirty-day session.
//
// Nothing here imports a Vercel package, so the whole gate can be driven by
// `node --test` with real signed cookies — middleware.js is a thin adapter over
// decide(), and the tests exercise decide() rather than reading the adapter.

import { SESSION_COOKIE, verifySession, readCookie, safeNextPath } from './session.mjs';
import { rosterFromEnv, roleOf, resolveCandidate } from './roster.mjs';

// The operator's chosen candidate. Not signed, deliberately: the value is only
// ever honoured for an operator, and only when it names a candidate already on
// the roster — so the worst a forged cookie can do is pick something the
// operator was entitled to pick anyway.
export const AS_COOKIE = 'desk_as';

// The sign-in flow is necessarily pre-auth: it is how somebody gets a session
// in the first place. Exact matches only — a startsWith here would open every
// path underneath them. An allow-list, not a deny-list: a page added tomorrow
// and forgotten is protected by default rather than public with nothing saying so.
export const PUBLIC_PATHS = ['/login', '/api/auth/google', '/api/auth/callback/google'];

// Returns null (not signed in, or signed in but no longer on the roster), or
//   { email, role, candidate: { email, id } | null, roster }
export async function identify({ cookieHeader, env, now = Date.now() }) {
  const session = await verifySession(env.DESK_SESSION_SECRET || '', readCookie(cookieHeader, SESSION_COOKIE), now);
  if (!session) return null;

  const roster = await rosterFromEnv(env);
  const role = roleOf(session.email, roster);
  if (!role) return null;

  const candidate = resolveCandidate(session.email, roster, readCookie(cookieHeader, AS_COOKIE));
  return { email: session.email, role, candidate, roster };
}

// What the gate does with a request. Pure, so it can be tested.
//   { action: 'pass' }
//   { action: 'unauthorized' }              — an API call: JSON 401 it can act on
//   { action: 'redirect', to: '/login?next=…' } — a page: go sign in, then come back
export async function decide({ pathname, search = '', cookieHeader, env, now = Date.now() }) {
  if (PUBLIC_PATHS.includes(pathname)) return { action: 'pass' };
  if (await identify({ cookieHeader, env, now })) return { action: 'pass' };

  // Redirecting an API call would hand fetch() a login PAGE with a 200, and the
  // client would try to parse HTML as JSON and report something unrelated.
  if (pathname.startsWith('/api/')) return { action: 'unauthorized' };

  // Remember where they were headed, so a link straight to /onboard survives
  // the sign-in round trip. Passed through the same guard the callback applies,
  // so a hostile value cannot even be put into the link.
  let to = '/login';
  if (pathname && pathname !== '/') {
    to += '?next=' + encodeURIComponent(safeNextPath(pathname + search));
  }
  return { action: 'redirect', to };
}
