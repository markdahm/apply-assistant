// Reading a FIXED-pathname blob without spending a list() call.
//
// On Vercel Blob, put/copy/list are the metered "simple operations";
// downloading a blob by its URL is not. Both read paths here used to list()
// first purely to discover the URL of a blob whose pathname we already knew.
// The Desk hits these hard — once per page load, and once every few seconds
// for up to two minutes while a cover letter is being written — so that
// lookup was the bulk of the account's operation count.
//
// Fixed-pathname blobs have a deterministic URL because publish.py writes them
// with x-add-random-suffix: 0. Shape confirmed against the live store on
// 27 August 2026:
//   https://<BLOB_STORE_ID minus "store_", lowercased>.private.blob.vercel-storage.com/<pathname>

function derivedUrl(pathname) {
  const id = (process.env.BLOB_STORE_ID || '').replace(/^store_/, '').toLowerCase();
  return id ? 'https://' + id + '.private.blob.vercel-storage.com/' + pathname : null;
}

// Returns the blob's text, or null if it genuinely does not exist yet.
// A 404 is "nothing published yet" and costs nothing to establish. Any other
// failure falls back to the old list() path, so an unexpected URL shape
// degrades to working-but-metered rather than to an empty Desk.
async function readFixed(pathname) {
  const token = process.env.BLOB_READ_WRITE_TOKEN;
  const auth = { Authorization: 'Bearer ' + token };
  const url = derivedUrl(pathname);

  if (url) {
    let r = null;
    try {
      // The store is private: a blob URL 403s without the bearer token.
      r = await fetch(url + '?v=' + Date.now(), { cache: 'no-store', headers: auth });
    } catch (e) {
      r = null; // network problem — fall through to the list() path
    }
    if (r && r.ok) return await r.text();
    if (r && r.status === 404) return null;
    if (r) {
      // Loud, because this is the difference between free and metered reads.
      console.error('readFixed: derived URL for %s returned %d — falling back to list()',
        pathname, r.status);
    }
  } else {
    console.error('readFixed: no BLOB_STORE_ID — falling back to list() for %s', pathname);
  }

  const { list } = require('@vercel/blob');
  const { blobs } = await list({ prefix: pathname });
  const hit = blobs.find((b) => b.pathname === pathname);
  if (!hit) return null;
  const r2 = await fetch(hit.url + '?v=' + Date.now(), { cache: 'no-store', headers: auth });
  return r2.ok ? await r2.text() : null;
}

module.exports = { readFixed, derivedUrl };
