// Start a Google sign-in.
//
// Sets a short-lived state cookie and sends the browser to Google. The state is
// what ties the callback to this request: without it, anyone could hand your
// browser a callback URL of their choosing and have it accepted. The same
// cookie carries the PKCE verifier, whose challenge goes to Google; the
// verifier never leaves this server, so an authorization code intercepted
// anywhere between here and the callback cannot be exchanged by whoever took it.
//
// Public route (middleware.js lets it through): it is how somebody gets a
// session in the first place.

const { lib, secure } = require('../_who');

module.exports = async (req, res) => {
  const {
    OAUTH_STATE_COOKIE, googleAuthUrl, newState, newVerifier, challengeFor,
    encodeAuthState, callbackUrlFor, safeNextPath, cookieHeader,
  } = await lib();

  const clientId = process.env.DESK_GOOGLE_CLIENT_ID || '';
  if (!clientId) {
    res.statusCode = 302;
    res.setHeader('Location', '/login?error=not_configured');
    return res.end();
  }

  const host = req.headers['x-forwarded-host'] || req.headers.host;
  const origin = (secure(req) ? 'https://' : 'http://') + host;
  const url = new URL(req.url, origin);
  const next = safeNextPath(url.searchParams.get('next'));
  const state = newState();
  const verifier = newVerifier();

  // State, verifier and the post-login destination ride together, so the
  // callback needs no query string of its own to trust. Ten minutes is long
  // enough to sign in and short enough that a stale one is not lying around.
  res.setHeader('Set-Cookie', cookieHeader(
    OAUTH_STATE_COOKIE, encodeAuthState({ state, verifier, next }),
    { maxAge: 600, secure: secure(req) },
  ));
  res.statusCode = 302;
  res.setHeader('Location', googleAuthUrl({
    clientId,
    redirectUri: callbackUrlFor(origin),
    state,
    codeChallenge: await challengeFor(verifier),
  }));
  return res.end();
};
