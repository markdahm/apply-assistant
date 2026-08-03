// Manual link intake for The Desk. Each queued link is its OWN blob object
// (inbox/<id>.json) — blob overwrites propagate slowly (~60s) and a shared
// array does read-modify-write, which can drop concurrent adds. One object
// per link has no overwrites, so no lost updates. The worker on your-server
// lists the prefix every ~10 minutes and processes anything new; duplicates
// are harmless (the pipeline upserts by URL).

const { put, list } = require('@vercel/blob');

function readBody(req) {
  return new Promise((resolve) => {
    let data = '';
    req.on('data', (c) => { data += c; if (data.length > 100000) req.destroy(); });
    req.on('end', () => resolve(data));
  });
}

module.exports = async (req, res) => {
  res.setHeader('Cache-Control', 'no-store');
  res.setHeader('Content-Type', 'application/json');
  try {
    if (req.method === 'GET') {
      const { blobs } = await list({ prefix: 'inbox/', limit: 200 });
      const entries = await Promise.all(blobs.slice(-60).map(async (b) => {
        try {
          const r = await fetch(b.url, { cache: 'no-store' });
          return r.ok ? await r.json() : null;
        } catch (e) { return null; }
      }));
      return res.end(JSON.stringify(entries.filter(Boolean)));
    }
    if (req.method === 'POST') {
      let body;
      try { body = JSON.parse(await readBody(req)); } catch (e) { body = null; }
      const links = body && Array.isArray(body.links) ? body.links : [];
      const clean = links
        .map((u) => String(u || '').trim().replace(/[),.;]+$/, ''))
        .map((u) => (/^https?:\/\//i.test(u) ? u : (/^[\w][\w.-]*\.[a-z]{2,}([\/?#]|$)/i.test(u) ? 'https://' + u : '')))
        .filter((u) => /^https?:\/\/\S+$/.test(u) && u.length < 2000)
        .slice(0, 30);
      if (!clean.length) { res.statusCode = 400; return res.end('{"error":"no valid links"}'); }
      const seen = new Set();
      let queued = 0;
      for (const url of clean) {
        if (seen.has(url)) continue;
        seen.add(url);
        const id = Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
        await put('inbox/' + id + '.json', JSON.stringify({ id, url, addedAt: Date.now() }), {
          access: 'public', addRandomSuffix: false, contentType: 'application/json',
        });
        queued += 1;
      }
      return res.end(JSON.stringify({ ok: true, queued, duplicates: clean.length - queued }));
    }
    res.statusCode = 405;
    return res.end('{"error":"GET or POST"}');
  } catch (e) {
    res.statusCode = 500;
    return res.end('{"error":"storage error"}');
  }
};
