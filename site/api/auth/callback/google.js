// Finish a Google sign-in.
//
// Exchanges the code, checks the token's claims, checks the address against the
// roster, and only then mints the session cookie everything downstream reads.
//
// Every failure sends the person back to /login with a short code and writes
// the real reason to the function log. Generic to the caller, specific to the
// log: collapsing those two is how the only evidence gets thrown away. The
// codes are the keys of the message table in login.html — tests/gate.test.mjs
// fails if this file can emit a code that page cannot explain.

const { lib, secure } = require('../../_who');

// Every refusal leaves through here. The address IS named in the log when it
// is known — knowing who tried is the point of having a roster — and absent
// when the refusal came before anybody's identity was established.
function back(res, error, logLine) {
  console.error('google sign-in refused: ' + logLine);
  res.statusCode = 302;
  res.setHeader('Location', '/login?error=' + encodeURIComponent(error));
  return res.end();
}

module.exports = async (req, res) => {
  const {
    OAUTH_STATE_COOKIE, GOOGLE_TOKEN_ENDPOINT, callbackUrlFor, decodeAuthState,
    decodeIdToken, verifyIdTokenClaims, rosterFromEnv, roleOf,
    SESSION_COOKIE, SESSION_TTL_MS, signSession, safeNextPath, timingSafeEqual,
    cookieHeader, readCookie,
  } = await lib();

  const host = req.headers['x-forwarded-host'] || req.headers.host;
  const origin = (secure(req) ? 'https://' : 'http://') + host;
  const url = new URL(req.url, origin);

  const clientId = process.env.DESK_GOOGLE_CLIENT_ID || '';
  const clientSecret = process.env.DESK_GOOGLE_CLIENT_SECRET || '';
  const sessionSecret = process.env.DESK_SESSION_SECRET || '';
  const roster = await rosterFromEnv(process.env);
  const rosterSize = roster.operators.length + roster.candidates.length;

  if (!clientId || !clientSecret || !sessionSecret || !rosterSize) {
    return back(res, 'not_configured',
      `missing config: clientId=${!!clientId} secret=${!!clientSecret} session=${!!sessionSecret} roster=${rosterSize}`);
  }

  if (url.searchParams.get('error')) {
    return back(res, 'declined', 'google returned ' + url.searchParams.get('error'));
  }

  // State: must be present, must match the cookie.
  const started = decodeAuthState(decodeURIComponent(readCookie(req.headers.cookie, OAUTH_STATE_COOKIE)));
  if (!started) return back(res, 'bad_state', 'state cookie missing or unreadable');
  const gotState = url.searchParams.get('state') || '';
  if (!timingSafeEqual(gotState, started.state)) {
    return back(res, 'bad_state', 'state cookie did not match the callback');
  }

  const code = url.searchParams.get('code') || '';
  if (!code) return back(res, 'no_code', 'no authorization code on the callback');

  let idToken;
  try {
    const r = await fetch(GOOGLE_TOKEN_ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        code,
        client_id: clientId,
        client_secret: clientSecret,
        redirect_uri: callbackUrlFor(origin),
        grant_type: 'authorization_code',
        // PKCE. Google checks this against the challenge sent at the start; a
        // code presented without the matching verifier is refused there.
        code_verifier: started.verifier,
      }),
    });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) {
      return back(res, 'exchange_failed', `token endpoint ${r.status}: ${JSON.stringify(body).slice(0, 300)}`);
    }
    idToken = body.id_token;
  } catch (err) {
    return back(res, 'exchange_failed', 'token exchange threw: ' + String(err));
  }

  const claims = verifyIdTokenClaims(decodeIdToken(idToken), clientId);
  if (!claims.ok) return back(res, 'bad_token', claims.reason);

  const role = roleOf(claims.email, roster);
  if (!role) {
    return back(res, 'not_allowed', `${claims.email} is on neither DESK_OPERATORS nor DESK_CANDIDATES`);
  }

  // The address goes INTO the session, so the gate can re-check the roster on
  // every request rather than trusting a decision made up to thirty days ago.
  res.setHeader('Set-Cookie', [
    cookieHeader(SESSION_COOKIE, await signSession(sessionSecret, claims.email),
      { maxAge: Math.floor(SESSION_TTL_MS / 1000), secure: secure(req) }),
    cookieHeader(OAUTH_STATE_COOKIE, '', { maxAge: 0, secure: secure(req) }),
  ]);
  console.log('google sign-in: ' + claims.email + ' (' + role + ')');
  res.statusCode = 302;
  res.setHeader('Location', safeNextPath(started.next));
  return res.end();
};
