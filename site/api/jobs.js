// Dynamic desk data. The worker on your-server publishes the freshest export to
// Blob (desk-data-live.json) after processing inbox links or daily sweeps —
// the app fetches this at boot and uses it when newer than the static bundle.

const { list } = require('@vercel/blob');

const PATHNAME = 'desk-data-live.json';

module.exports = async (req, res) => {
  res.setHeader('Cache-Control', 'no-store');
  res.setHeader('Content-Type', 'application/json');
  try {
    const { blobs } = await list({ prefix: PATHNAME });
    const hit = blobs.find((b) => b.pathname === PATHNAME);
    if (!hit) return res.end('{}');
    const r = await fetch(hit.url + '?v=' + Date.now(), { cache: 'no-store' });
    return res.end(r.ok ? (await r.text() || '{}') : '{}');
  } catch (e) {
    res.statusCode = 500;
    return res.end('{"error":"storage error"}');
  }
};
