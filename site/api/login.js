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
  if (ok) {
    const tok = crypto.createHash('sha256').update(real).digest('hex');
    res.setHeader('Set-Cookie',
      'desk=' + tok + '; Path=/; Max-Age=7776000; HttpOnly; Secure; SameSite=Lax');
    res.statusCode = 302;
    res.setHeader('Location', '/');
    return res.end();
  }
  res.statusCode = 302;
  res.setHeader('Location', '/login?bad=1');
  return res.end();
};
