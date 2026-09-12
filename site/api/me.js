// Who am I, and whose Desk am I looking at.
//
// GET  → { email, role, candidate: {email,id}|null, candidates?: [{email,id}] }
//        `candidates` is present for operators only: a candidate is never told
//        who else is on the roster.
// POST → { as: "<candidate id>" } — operators only. Sets the desk_as cookie so
//        every later request resolves to that candidate. Refused for a
//        candidate (they are always themselves) and for an id not on the
//        roster (the cookie is honoured only for roster candidates anyway, so
//        this refusal is a courtesy, not the control).
//
// The Desk calls GET before loading anything else: it decides which localStorage
// bucket holds this candidate's decisions, and whether to show an operator the
// picker instead of a queue.

const { lib, secure, identify } = require('./_who');

function readBody(req) {
  return new Promise((resolve) => {
    let data = '';
    req.on('data', (c) => { data += c; if (data.length > 4000) req.destroy(); });
    req.on('end', () => resolve(data));
  });
}

module.exports = async (req, res) => {
  res.setHeader('Cache-Control', 'no-store');
  res.setHeader('Content-Type', 'application/json');

  const who = await identify(req);
  if (!who) {
    res.statusCode = 401;
    return res.end('{"ok":false,"error":"not signed in"}');
  }

  if (req.method === 'GET') {
    const out = { ok: true, email: who.email, role: who.role, candidate: who.candidate };
    if (who.role === 'operator') out.candidates = who.roster.candidates;
    return res.end(JSON.stringify(out));
  }

  if (req.method === 'POST') {
    if (who.role !== 'operator') {
      res.statusCode = 403;
      return res.end('{"ok":false,"error":"only an operator can switch candidates"}');
    }
    let body;
    try { body = JSON.parse(await readBody(req)); } catch (e) { body = null; }
    const as = String((body && body.as) || '').trim();
    const hit = who.roster.candidates.find((c) => c.id === as);
    if (!hit) {
      res.statusCode = 400;
      return res.end('{"ok":false,"error":"that candidate is not on the roster"}');
    }
    const { AS_COOKIE, cookieHeader } = await lib();
    res.setHeader('Set-Cookie', cookieHeader(AS_COOKIE, hit.id,
      { maxAge: 90 * 24 * 60 * 60, secure: secure(req) }));
    return res.end(JSON.stringify({ ok: true, candidate: hit }));
  }

  res.statusCode = 405;
  return res.end('{"ok":false,"error":"GET or POST"}');
};
