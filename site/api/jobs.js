// Dynamic desk data. The worker on the pipeline host publishes the freshest
// export to Blob (desk-data-live.json) after processing letter requests or
// daily sweeps — the app fetches this at boot and uses it when newer than the
// static bundle.
//
// Reads go through readFixed(), which knows this blob's URL and therefore
// spends no metered list() call. See api/_blobread.js.

const { readFixed } = require('./_blobread');

const PATHNAME = 'desk-data-live.json';

module.exports = async (req, res) => {
  res.setHeader('Cache-Control', 'no-store');
  res.setHeader('Content-Type', 'application/json');
  try {
    const text = await readFixed(PATHNAME);
    return res.end(text || '{}');
  } catch (e) {
    // Generic to the client, specific to the log — the log is the only
    // evidence there will be.
    console.error('api/jobs failed:', e && e.stack ? e.stack : e);
    res.statusCode = 500;
    return res.end('{"error":"storage error"}');
  }
};
