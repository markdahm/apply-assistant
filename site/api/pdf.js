// HTML -> PDF for The Desk (resume + cover letter downloads, including edits).
// POST { html: "<body fragment or full page>", autofit: true } -> application/pdf.
// Auth rides the desk-cookie middleware. Auto-fits to one Letter page by
// retrying at smaller zooms (same trick as the local builder).

// @sparticuz/chromium v149+ is ESM-only — load both lazily via dynamic import.
let _deps = null;
async function deps() {
  if (!_deps) {
    const [c, p] = await Promise.all([import('@sparticuz/chromium'), import('puppeteer-core')]);
    _deps = { chromium: c.default || c, puppeteer: p.default || p };
  }
  return _deps;
}

function readBody(req) {
  return new Promise((resolve) => {
    let data = '';
    req.on('data', (c) => { data += c; if (data.length > 900000) req.destroy(); });
    req.on('end', () => resolve(data));
  });
}

function pageCount(buf) {
  const m = String(buf.toString('latin1')).match(/\/Count\s+(\d+)/g);
  if (!m) return 1;
  return Math.max(...m.map((x) => parseInt(x.replace(/\D+/g, ''), 10) || 1));
}

function wrap(html, zoom) {
  if (/^\s*<!doctype/i.test(html) || /<html[\s>]/i.test(html)) return html;
  return '<!doctype html><html><head><meta charset="utf-8"><style>'
    + '@page{size:letter;margin:0.45in} html,body{margin:0;padding:0;background:#fff}'
    + 'body{zoom:' + zoom + '} body>div{border:none !important;box-shadow:none !important;padding:0 !important}'
    + '[contenteditable]{outline:none}'
    + '</style></head><body>' + html + '</body></html>';
}

module.exports = async (req, res) => {
  if (req.method !== 'POST') { res.statusCode = 405; return res.end('POST only'); }
  let body;
  try { body = JSON.parse(await readBody(req)); } catch (e) { body = null; }
  const html = body && typeof body.html === 'string' ? body.html : '';
  if (!html || html.length < 40) { res.statusCode = 400; return res.end('missing html'); }
  const autofit = body.autofit !== false;

  let browser;
  try {
    const { chromium, puppeteer } = await deps();
    browser = await puppeteer.launch({
      args: chromium.args,
      defaultViewport: chromium.defaultViewport,
      executablePath: await chromium.executablePath(),
      headless: chromium.headless,
    });
    const page = await browser.newPage();
    const zooms = autofit ? [1.0, 0.9, 0.82, 0.75] : [1.0];
    let pdf = null;
    for (const z of zooms) {
      await page.setContent(wrap(html, z), { waitUntil: 'networkidle0', timeout: 20000 });
      pdf = await page.pdf({ format: 'letter', printBackground: true, preferCSSPageSize: true });
      if (pageCount(pdf) <= 1) break;
    }
    res.setHeader('Content-Type', 'application/pdf');
    res.setHeader('Cache-Control', 'no-store');
    return res.end(pdf);
  } catch (e) {
    res.statusCode = 500;
    return res.end('pdf error: ' + (e && e.message ? e.message.slice(0, 300) : String(e)));
  } finally {
    if (browser) { try { await browser.close(); } catch (e) {} }
  }
};
