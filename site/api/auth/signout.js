// Sign out.
//
// The cookie is the whole session — clearing it is the whole sign-out, and
// there is nothing to tell Google, because this app asked for no ongoing grant
// (access_type is 'online' and no refresh token was ever issued). The
// operator's candidate choice goes with it, so the next sign-in starts clean.
//
// Behind the gate on purpose: signing out is something only a signed-in
// browser can do. POST only, so a hostile page cannot sign somebody out with an
// image tag.

const { lib, secure, identify } = require('../_who');

module.exports = async (req, res) => {
  if (req.method !== 'POST') {
    res.statusCode = 405;
    return res.end('POST only');
  }
  const { SESSION_COOKIE, AS_COOKIE, cookieHeader } = await lib();
  const who = await identify(req);
  console.log('sign-out: ' + (who ? who.email : '(no session)'));

  // Same attributes the cookies were set with. A browser matches a deletion to
  // the cookie it replaces on name, path and Secure, so clearing with a
  // different shape leaves the original in place.
  res.setHeader('Set-Cookie', [
    cookieHeader(SESSION_COOKIE, '', { maxAge: 0, secure: secure(req) }),
    cookieHeader(AS_COOKIE, '', { maxAge: 0, secure: secure(req) }),
  ]);
  res.setHeader('Content-Type', 'application/json');
  return res.end('{"ok":true}');
};
