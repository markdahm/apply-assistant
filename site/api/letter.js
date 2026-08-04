// On-demand cover letters. The Desk's "write this one" button posts a job uid
// here; each request becomes its OWN blob under letter-requests/ (same reason
// as api/inbox: blob overwrites propagate slowly and a shared array does
// read-modify-write, which drops concurrent clicks).
//
// Nothing is generated here. The worker on the pipeline host claims the
// request and runs the real letters.py, which carries the honesty validators
// (every number checked against the fact sources, AI-tell phrases banned,
// employer required, fail closed to a placeholder). Generating in this
// function would fork that logic into a second implementation and require
// shipping the candidate's resume, voice file, and an Anthropic key to Vercel.
//
// GET reports how many requests are still outstanding, so the UI can tell
// "still working" apart from "the worker isn't running".

const { put, list } = require('@vercel/blob');

const MAX_BODY = 8000;

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

module.exports = async (req, res) => {
  res.setHeader('Cache-Control', 'no-store');
  res.setHeader('Content-Type', 'application/json');
  try {
    if (req.method === 'GET') {
      const { blobs } = await list({ prefix: 'letter-requests/', limit: 200 });
      return res.end(JSON.stringify({
        pending: blobs.length,
        oldest: blobs.length
          ? blobs.map((b) => b.uploadedAt).sort()[0]
          : null,
      }));
    }

    if (req.method === 'POST') {
      let body;
      try {
        body = JSON.parse(await readBody(req));
      } catch (e) {
        res.statusCode = e && e.message === 'too large' ? 413 : 400;
        return res.end('{"error":"could not read that request"}');
      }
      // uids are content hashes from the pipeline — hex, fixed width.
      const uid = String((body && body.uid) || '').trim();
      if (!/^[a-f0-9]{8,64}$/i.test(uid)) {
        res.statusCode = 400;
        return res.end('{"error":"a valid job uid is required"}');
      }
      const id = Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
      await put('letter-requests/' + id + '.json', JSON.stringify({
        id, uid, requestedAt: Date.now(),
      }), {
        access: 'private', addRandomSuffix: false, contentType: 'application/json',
      });
      return res.end(JSON.stringify({ ok: true, id, uid }));
    }

    res.statusCode = 405;
    return res.end('{"error":"GET or POST"}');
  } catch (e) {
    console.error('[letter] failed:', (e && e.message) || e,
      '| has rw token:', Boolean(process.env.BLOB_READ_WRITE_TOKEN));
    res.statusCode = 500;
    return res.end('{"error":"storage error"}');
  }
};

// Exported for the local round-trip test.
module.exports.MAX_BODY = MAX_BODY;
