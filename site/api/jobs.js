// Dynamic desk data for ONE candidate. The pipeline on Mark's machine publishes
// the freshest export to Blob under that candidate's prefix
// (c/<id>/desk-data-live.json) after processing letter requests or a sweep —
// the app fetches this at boot.
//
// Which candidate is decided by the signed-in identity (api/_who.js), never by
// a parameter. Reads go through readFixed(), which knows the blob's URL and
// therefore spends no metered list() call. See api/_blobread.js.

const { readFixed } = require('./_blobread');
const { requireCandidate } = require('./_who');

const NAME = 'desk-data-live.json';

module.exports = async (req, res) => {
  res.setHeader('Cache-Control', 'no-store');
  const c = await requireCandidate(req, res);
  if (!c) return;
  res.setHeader('Content-Type', 'application/json');
  try {
    const text = await readFixed(c.path(NAME));
    return res.end(text || '{}');
  } catch (e) {
    // Generic to the client, specific to the log — the log is the only
    // evidence there will be.
    console.error('api/jobs failed:', e && e.stack ? e.stack : e);
    res.statusCode = 500;
    return res.end('{"error":"storage error"}');
  }
};
