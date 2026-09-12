// Status persistence for The Desk. One JSON map per candidate in Vercel Blob,
// at c/<id>/status.json:
//   { "<jobId>": { "status": "Applied", "decidedAt": 1699999999999 }, ... }
// GET returns the map (cache-busted read so it's always fresh); POST overwrites
// it with the client's full map — one candidate, one browser at a time, last
// write wins. Which candidate is decided by the signed-in identity.

const { put } = require('@vercel/blob');
const { readFixed } = require('./_blobread');
const { requireCandidate } = require('./_who');

const NAME = 'status.json';
const ALLOWED = new Set(['Interested', 'Applied', 'Interviewing', 'Ignored', 'Not interested']);

function readBody(req) {
  return new Promise((resolve) => {
    let data = '';
    req.on('data', (c) => { data += c; if (data.length > 900000) req.destroy(); });
    req.on('end', () => resolve(data));
  });
}

module.exports = async (req, res) => {
  res.setHeader('Cache-Control', 'no-store');
  const c = await requireCandidate(req, res);
  if (!c) return;
  try {
    if (req.method === 'GET') {
      // readFixed knows this blob's URL, so the read spends no metered
      // list() call. See api/_blobread.js.
      const text = await readFixed(c.path(NAME));
      res.setHeader('Content-Type', 'application/json');
      return res.end(text || '{}');
    }

    if (req.method === 'POST') {
      const raw = await readBody(req);
      let map;
      try { map = JSON.parse(raw); } catch (e) { map = null; }
      if (!map || typeof map !== 'object' || Array.isArray(map)) {
        res.statusCode = 400;
        return res.end('bad body');
      }
      const clean = {};
      for (const [id, v] of Object.entries(map)) {
        if (typeof id !== 'string' || id.length > 64 || !v) continue;
        const entry = {};
        const status = String(v.status || '');
        if (ALLOWED.has(status)) {
          entry.status = status;
          entry.decidedAt = Number(v.decidedAt) || Date.now();
        }
        if (typeof v.cover === 'string' && v.cover.length <= 8000) entry.cover = v.cover;
        if (typeof v.resumeHtml === 'string' && v.resumeHtml.length <= 24000) entry.resumeHtml = v.resumeHtml;
        if (Object.keys(entry).length) clean[id] = entry;
        if (Object.keys(clean).length >= 2000) break;
      }
      await put(c.path(NAME), JSON.stringify(clean), {
        access: 'private',
        addRandomSuffix: false,
        allowOverwrite: true,
        contentType: 'application/json',
        cacheControlMaxAge: 60,
      });
      res.setHeader('Content-Type', 'application/json');
      return res.end(JSON.stringify({ ok: true, count: Object.keys(clean).length }));
    }

    res.statusCode = 405;
    return res.end('GET or POST');
  } catch (e) {
    console.error('api/status failed:', (e && e.message) || e);
    res.statusCode = 500;
    return res.end('storage error');
  }
};
