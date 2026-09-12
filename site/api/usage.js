// API consumption, for the operator's ops page.
//
// The paid calls — Anthropic, Firecrawl, JSearch, Blob — happen on the
// pipeline host, not here. Each checkout's pipeline appends a ledger and, on
// every publish, puts a rollup at ops/usage/<candidate-id>.json (see
// apply_assistant/usage.py). This function lists that prefix, fetches every
// file, and hands them back unmerged: the page sums them and also shows each
// checkout on its own, because a JSearch quota is per key and two candidates
// on one key spend the same 200.
//
// Operators only. A candidate gets a 403, never an empty list — an empty list
// would read as "no usage", which is the lie this whole page exists to avoid.
//
// One metered list() per page load plus one un-metered download per file.
// The page is opened by one person a few times a day; that is the budget.

const { list } = require('@vercel/blob');
const { requireOperator } = require('./_who');

const PREFIX = 'ops/usage/';

module.exports = async (req, res) => {
  res.setHeader('Cache-Control', 'no-store');
  const who = await requireOperator(req, res);
  if (!who) return;
  res.setHeader('Content-Type', 'application/json');
  try {
    const { blobs } = await list({ prefix: PREFIX, limit: 50 });
    const files = await Promise.all(blobs.map(async (b) => {
      try {
        const r = await fetch(b.url + '?v=' + Date.now(), {
          cache: 'no-store',
          headers: { Authorization: 'Bearer ' + process.env.BLOB_READ_WRITE_TOKEN },
        });
        if (!r.ok) return { pathname: b.pathname, uploadedAt: b.uploadedAt, error: 'HTTP ' + r.status };
        return { pathname: b.pathname, uploadedAt: b.uploadedAt, rollup: await r.json() };
      } catch (e) {
        return { pathname: b.pathname, uploadedAt: b.uploadedAt, error: String((e && e.message) || e) };
      }
    }));
    // Name each file by the candidate it belongs to, when the roster knows the id.
    const byId = new Map(who.roster.candidates.map((c) => [c.id, c.email]));
    for (const f of files) {
      const id = (f.pathname.slice(PREFIX.length).replace(/\.json$/, ''));
      f.candidateId = id;
      f.candidateEmail = byId.get(id) || null;
    }
    return res.end(JSON.stringify({ ok: true, fetchedAt: Date.now(), files }));
  } catch (e) {
    console.error('api/usage failed:', (e && e.message) || e);
    res.statusCode = 500;
    return res.end('{"ok":false,"error":"storage error"}');
  }
};
