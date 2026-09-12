// Identity for the Node handlers in api/.
//
// The shared logic lives in api/_lib/*.mjs (ESM, Web Crypto) so middleware.js
// can run the identical code on the Edge. These handlers are CommonJS, so they
// reach it with a dynamic import — a string literal, which Vercel's bundler
// traces like a require.
//
// middleware.js already refuses anyone who is not signed in and on the roster.
// The handlers ask AGAIN, for two reasons: a handler that trusts a gate it
// cannot see is one config mistake from being public, and the handler is the
// place that needs to know WHICH candidate — the gate only knows that somebody
// is allowed in.

// All four shared modules, flattened. The auth handlers destructure from this
// too — tests/handlers.test.mjs drives the real sign-in through it, which is
// what caught the first version forgetting google-auth.mjs entirely.
async function lib() {
  const who = await import('./_lib/who.mjs');
  const roster = await import('./_lib/roster.mjs');
  const session = await import('./_lib/session.mjs');
  const google = await import('./_lib/google-auth.mjs');
  return { ...who, ...roster, ...session, ...google };
}

// Wrap Node's header object so the shared helpers can call headers.get().
function asRequest(req) {
  return { headers: { get: (k) => req.headers[String(k).toLowerCase()] || null } };
}

function secure(req) {
  // Node handlers do not get an absolute URL; behind Vercel the proxy header
  // is always present, and a bare http URL is the honest fallback locally.
  const proto = req.headers['x-forwarded-proto'];
  if (proto) return String(proto).split(',')[0].trim().toLowerCase() === 'https';
  return false;
}

async function identify(req) {
  const { identify: id } = await lib();
  return id({ cookieHeader: req.headers.cookie || '', env: process.env });
}

// Resolve the candidate whose data this request may touch, or answer the
// request with the right refusal and return null. Every data handler starts
// with this; blob pathnames are then built with `path(name)`.
//
//   401  not signed in, or no longer on the roster
//   409  an operator who has not picked a candidate yet — the Desk shows a
//        picker for exactly this answer, and it must not look like "no jobs"
async function requireCandidate(req, res) {
  const who = await identify(req);
  if (!who) {
    res.statusCode = 401;
    res.setHeader('Content-Type', 'application/json');
    res.end('{"ok":false,"error":"not signed in"}');
    return null;
  }
  if (!who.candidate) {
    res.statusCode = 409;
    res.setHeader('Content-Type', 'application/json');
    res.end('{"ok":false,"error":"no candidate selected"}');
    return null;
  }
  const { blobPath } = await lib();
  const id = who.candidate.id;
  return { who, id, path: (name) => blobPath(id, name) };
}

// Operators only. Used by the ops page's data: usage across every candidate
// is the operator's business and nobody else's.
//   401  not signed in / off the roster
//   403  signed in as a candidate
async function requireOperator(req, res) {
  const who = await identify(req);
  if (!who) {
    res.statusCode = 401;
    res.setHeader('Content-Type', 'application/json');
    res.end('{"ok":false,"error":"not signed in"}');
    return null;
  }
  if (who.role !== 'operator') {
    res.statusCode = 403;
    res.setHeader('Content-Type', 'application/json');
    res.end('{"ok":false,"error":"operators only"}');
    return null;
  }
  return who;
}

module.exports = { lib, asRequest, secure, identify, requireCandidate, requireOperator };
