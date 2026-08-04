// Remote onboarding intake. The candidate fills in /onboard (behind the same
// password gate as the rest of the Desk) and their answers land here as ONE
// blob per submission — same shape as api/inbox.js, and for the same reason:
// no read-modify-write, so nothing can be lost to a concurrent write.
//
// `apply onboard --fetch` on the pipeline host lists the onboard/ prefix with
// BLOB_READ_WRITE_TOKEN, takes the newest, and writes config/profile.json and
// the profile/*.md files through the same save_all() the local form uses.
//
// This body holds real personal data (resume, contact details, comp floor). The
// store is configured PRIVATE, so the blob URL 403s without a bearer token — an
// unguessable URL is no longer the only thing protecting it. The random suffix
// stays as a second layer, and the page itself is gated by middleware.js.

const { put, list } = require('@vercel/blob');

const MAX_BODY = 400000; // a pasted resume + writing sample, with headroom

function readBody(req) {
  return new Promise((resolve, reject) => {
    let data = '';
    req.on('data', (c) => {
      data += c;
      if (data.length > MAX_BODY) {
        req.destroy();
        reject(new Error('too large'));
      }
    });
    req.on('end', () => resolve(data));
    req.on('error', reject);
  });
}

// Keep only the fields save_all() reads, so a malformed or padded post can't
// bloat the blob. Strings are capped; `employers` is a list of {name, url}.
const TEXT_FIELDS = {
  name: 200, email: 200, phone: 60, home_location: 200, summary: 2000,
  titles: 1000, skills: 2000, years_experience: 10, seniority: 40,
  work_authorization: 300, locations: 4000, seniority_floor: 40,
  seniority_ceiling: 40, comp_floor: 20, exclude_role_keywords: 2000,
  exclude_keywords: 2000, resume: 120000, voice: 60000, experience_bank: 60000,
};
const BOOL_FIELDS = ['remote_ok', 'needs_sponsorship'];

function clean(body) {
  const out = {};
  for (const [k, max] of Object.entries(TEXT_FIELDS)) {
    if (body[k] == null) continue;
    out[k] = String(body[k]).slice(0, max);
  }
  for (const k of BOOL_FIELDS) out[k] = Boolean(body[k]);
  const emps = Array.isArray(body.employers) ? body.employers : [];
  out.employers = emps
    .map((e) => ({
      name: String((e && e.name) || '').slice(0, 200),
      url: String((e && e.url) || '').trim().slice(0, 2000),
    }))
    .filter((e) => e.url)
    .slice(0, 60);
  return out;
}

module.exports = async (req, res) => {
  res.setHeader('Cache-Control', 'no-store');
  res.setHeader('Content-Type', 'application/json');
  try {
    // Default: metadata only, never the submitted personal data.
    // ?include=payload returns the newest submission so the form can pre-fill
    // and the candidate can refine their answers instead of retyping them.
    // Safe behind middleware.js — and the Desk already displays this person's
    // resume to anyone holding the password.
    if (req.method === 'GET') {
      const { blobs } = await list({ prefix: 'onboard/', limit: 100 });
      const out = {
        count: blobs.length,
        latest: blobs.length
          ? blobs.map((b) => b.uploadedAt).sort().slice(-1)[0]
          : null,
      };
      const url = new URL(req.url, 'http://x');
      if (url.searchParams.get('include') === 'payload' && blobs.length) {
        const newest = blobs.slice().sort(
          (a, b) => new Date(a.uploadedAt) - new Date(b.uploadedAt),
        ).pop();
        try {
          // Private store: the blob URL needs the bearer token.
          const r = await fetch(newest.url, {
            cache: 'no-store',
            headers: { Authorization: 'Bearer ' + process.env.BLOB_READ_WRITE_TOKEN },
          });
          if (r.ok) {
            const entry = await r.json();
            out.payload = entry.payload || null;
            out.submittedAt = entry.submittedAt || null;
          }
        } catch (e) {
          out.payload = null;   // pre-fill is a convenience; never fail the page
        }
      }
      return res.end(JSON.stringify(out));
    }

    if (req.method === 'POST') {
      let body;
      try {
        body = JSON.parse(await readBody(req));
      } catch (e) {
        res.statusCode = e && e.message === 'too large' ? 413 : 400;
        return res.end(JSON.stringify({ error: 'could not read that submission' }));
      }
      if (!body || typeof body !== 'object') {
        res.statusCode = 400;
        return res.end('{"error":"expected a JSON object"}');
      }
      const payload = clean(body);
      if (!payload.name || !payload.name.trim()) {
        res.statusCode = 400;
        return res.end('{"error":"a name is required"}');
      }
      const id = Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
      await put('onboard/' + id + '.json', JSON.stringify({
        id, submittedAt: Date.now(), payload,
      }), {
        access: 'private', addRandomSuffix: true, contentType: 'application/json',
      });
      return res.end(JSON.stringify({
        ok: true, id, employers: payload.employers.length,
      }));
    }

    res.statusCode = 405;
    return res.end('{"error":"GET or POST"}');
  } catch (e) {
    // Log the real cause to the function log — a bare "storage error" told us
    // nothing when this first failed in production. The CLIENT still gets the
    // generic message: the reason can name internals, and this endpoint is
    // reachable by the candidate.
    console.error('[onboard] failed:', (e && e.message) || e,
      '| has rw token:', Boolean(process.env.BLOB_READ_WRITE_TOKEN),
      '| has store id:', Boolean(process.env.BLOB_STORE_ID),
      '| has oidc:', Boolean(process.env.VERCEL_OIDC_TOKEN));
    res.statusCode = 500;
    return res.end('{"error":"storage error"}');
  }
};

// Exported so the round-trip test can check this against save_all() in Python
// without deploying — the two halves must agree on the payload shape.
module.exports.clean = clean;
