// The five source files the pipeline runs on, per candidate, editable from the
// Desk's Settings page.
//
//   GET  → { files: [{ name, content|null, updatedAt, by, size }] }
//   PUT  { name, content } → { ok, name, updatedAt }
//
// Stored at c/<id>/config/<name>. WHICH candidate is decided by the signed-in
// identity (api/_who.js) — a candidate edits only their own five files, an
// operator edits the picked candidate's. A sidecar c/<id>/config/_meta.json
// records who saved each file and when; apply_assistant/configsync.py reads
// the same sidecar and writes its own entries when the pipeline pushes.
//
// Validation is the same as the pipeline's `configsync.validate`: a size cap,
// the two .json files must parse to objects, and profile.json must carry its
// "candidate" and "preferences" sections. The pipeline validates again on
// pull and keeps its local copy if a bad file ever gets through — this check
// is what turns a typo into a red line under the text box instead of a
// broken sweep.

const { put } = require('@vercel/blob');
const { readFixed } = require('./_blobread');
const { requireCandidate } = require('./_who');

// Must match configsync.FILES. tests/test_configsync.py compares the two.
const FILES = ['profile.json', 'sources.json', 'resume.md', 'experience_bank.md', 'voice_real.md'];
const MAX_BYTES = 250000;
const META = 'config/_meta.json';

function validate(name, content) {
  if (Buffer.byteLength(content, 'utf8') > MAX_BYTES) return `too large (limit ${MAX_BYTES} bytes)`;
  if (name.endsWith('.json')) {
    let doc;
    try { doc = JSON.parse(content); } catch (e) { return 'not valid JSON: ' + String(e.message).slice(0, 120); }
    if (!doc || typeof doc !== 'object' || Array.isArray(doc)) return 'must be a JSON object';
    if (name === 'profile.json') {
      for (const k of ['candidate', 'preferences']) {
        if (!doc[k] || typeof doc[k] !== 'object' || Array.isArray(doc[k])) return `profile.json needs a "${k}" object`;
      }
    }
  }
  return null;
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let data = '';
    req.on('data', (c) => { data += c; if (data.length > MAX_BYTES + 4096) { req.destroy(); reject(new Error('too large')); } });
    req.on('end', () => resolve(data));
    req.on('error', reject);
  });
}

module.exports = async (req, res) => {
  res.setHeader('Cache-Control', 'no-store');
  const c = await requireCandidate(req, res);
  if (!c) return;
  res.setHeader('Content-Type', 'application/json');
  try {
    let meta = {};
    try { meta = JSON.parse((await readFixed(c.path(META))) || '{}') || {}; } catch (e) { meta = {}; }

    if (req.method === 'GET') {
      const files = await Promise.all(FILES.map(async (name) => {
        const content = await readFixed(c.path('config/' + name));
        const m = meta[name] || {};
        return { name, content, updatedAt: m.updatedAt || null, by: m.by || null,
          size: content == null ? null : Buffer.byteLength(content, 'utf8') };
      }));
      return res.end(JSON.stringify({ ok: true, candidate: c.who.candidate.email, files }));
    }

    if (req.method === 'PUT') {
      let body;
      try { body = JSON.parse(await readBody(req)); } catch (e) {
        res.statusCode = e && e.message === 'too large' ? 413 : 400;
        return res.end('{"ok":false,"error":"could not read that request"}');
      }
      const name = String((body && body.name) || '');
      if (!FILES.includes(name)) { res.statusCode = 400; return res.end('{"ok":false,"error":"not a config file"}'); }
      const content = typeof (body && body.content) === 'string' ? body.content : null;
      if (content == null) { res.statusCode = 400; return res.end('{"ok":false,"error":"content must be a string"}'); }
      const err = validate(name, content);
      if (err) { res.statusCode = 400; return res.end(JSON.stringify({ ok: false, error: err })); }

      await put(c.path('config/' + name), content, {
        access: 'private', addRandomSuffix: false, allowOverwrite: true,
        contentType: name.endsWith('.json') ? 'application/json' : 'text/markdown',
        cacheControlMaxAge: 60,
      });
      const updatedAt = Date.now();
      meta[name] = { updatedAt, by: c.who.email };
      await put(c.path(META), JSON.stringify(meta), {
        access: 'private', addRandomSuffix: false, allowOverwrite: true,
        contentType: 'application/json', cacheControlMaxAge: 60,
      });
      return res.end(JSON.stringify({ ok: true, name, updatedAt, by: c.who.email }));
    }

    res.statusCode = 405;
    return res.end('{"ok":false,"error":"GET or PUT"}');
  } catch (e) {
    console.error('api/config failed:', (e && e.message) || e);
    res.statusCode = 500;
    return res.end('{"ok":false,"error":"storage error"}');
  }
};

module.exports.FILES = FILES;
module.exports.validate = validate;
