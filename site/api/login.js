const crypto = require('crypto');

function readBody(req) {
  return new Promise((resolve) => {
    let data = '';
    req.on('data', (c) => { data += c; });
    req.on('end', () => resolve(data));
  });
}

module.exports = async (req, res) => {
  if (req.method !== 'POST') {
    res.statusCode = 405;
    return res.end('POST only');
  }
  const body = await readBody(req);
  const params = new URLSearchParams(body);
  const pw = params.get('password') || '';
  const real = process.env.DESK_PASSWORD || '';
  const ok = real && pw.length === real.length &&
    crypto.timingSafeEqual(Buffer.from(pw), Buffer.from(real));
  // Only ever bounce back to a path on this site — an absolute or scheme-
  // relative "next" would make this an open redirect. Backslashes are barred
  // too: some browsers read a leading "/\" as protocol-relative, so "/\evil.com"
  // would escape the site the same way "//evil.com" does.
  const raw = params.get('next') || '';
  const next = /^\/(?![/\\])[^\s\\]*$/.test(raw) ? raw : '/';

  if (ok) {
    const tok = crypto.createHash('sha256').update(real).digest('hex');
    res.setHeader('Set-Cookie',
      'desk=' + tok + '; Path=/; Max-Age=7776000; HttpOnly; Secure; SameSite=Lax');
    res.statusCode = 302;
    res.setHeader('Location', next);
    return res.end();
  }
  res.statusCode = 302;
  res.setHeader('Location', '/login?bad=1' +
    (next !== '/' ? '&next=' + encodeURIComponent(next) : ''));
  return res.end();
};
