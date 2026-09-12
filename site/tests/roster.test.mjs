// The roster: who gets in, whose data they see, where it lives.
//
//   cd site && npm test
//
// This is the multi-candidate boundary. Every case here is a way one person
// could end up looking at another person's job search, so the tests are
// written from the attacker's side: a candidate with a forged desk_as cookie,
// an operator asking for an id that is not on the roster, an empty roster
// that must admit nobody rather than everybody.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import {
  parseList, candidateId, rosterFromEnv, roleOf, resolveCandidate, blobPath, PREFIX_ROOT,
} from '../api/_lib/roster.mjs';
import { identify, AS_COOKIE } from '../api/_lib/who.mjs';
import { signSession, SESSION_COOKIE } from '../api/_lib/session.mjs';

const ENV = {
  DESK_OPERATORS: 'Mark@Example.com',
  DESK_CANDIDATES: 'ann@example.com, bea@example.com',
  DESK_SESSION_SECRET: 'test-secret',
};

// ── Ids ─────────────────────────────────────────────────────────────────────

test('the candidate id is sha256 of the normalised address, first 16 hex chars', async () => {
  // Computed independently, and this exact rule is what tenant.py implements.
  const expected = createHash('sha256').update('ann@example.com').digest('hex').slice(0, 16);
  assert.equal(await candidateId('ann@example.com'), expected);
  assert.equal(await candidateId('  ANN@Example.COM '), expected, 'case and padding must not change the id');
  assert.match(expected, /^[a-f0-9]{16}$/);
});

test('two addresses never share an id, and a Gmail dot variant is a different person', async () => {
  assert.notEqual(await candidateId('a@x.com'), await candidateId('b@x.com'));
  // Gmail treats dots as the same mailbox; this roster does not, deliberately —
  // string identity is the only rule that cannot surprise.
  assert.notEqual(await candidateId('first.last@gmail.com'), await candidateId('firstlast@gmail.com'));
});

// ── Lists and roles ─────────────────────────────────────────────────────────

test('an empty roster admits nobody', async () => {
  for (const raw of [undefined, '', '   ', ',', ' , , ']) {
    assert.deepEqual(parseList(raw), []);
  }
  const roster = await rosterFromEnv({});
  assert.equal(roleOf('mark@example.com', roster), null);
  assert.equal(resolveCandidate('mark@example.com', roster, ''), null);
});

test('commas, spaces and newlines all separate; matching ignores case and padding only', async () => {
  assert.deepEqual(parseList('a@x.com, b@y.com'), ['a@x.com', 'b@y.com']);
  assert.deepEqual(parseList('a@x.com\nb@y.com'), ['a@x.com', 'b@y.com']);
  const roster = await rosterFromEnv(ENV);
  assert.equal(roleOf('  MARK@EXAMPLE.COM ', roster), 'operator');
  assert.equal(roleOf('ann@example.com', roster), 'candidate');
  for (const bad of [
    'ann@example.com.evil.com', 'evil.com/ann@example.com', 'ann@example.co',
    'a.nn@example.com', 'ann+x@example.com', '', undefined, null,
  ]) {
    assert.equal(roleOf(bad, roster), null, `admitted ${JSON.stringify(bad)}`);
  }
});

test('an address on both lists is an operator', async () => {
  const roster = await rosterFromEnv({ ...ENV, DESK_CANDIDATES: ENV.DESK_CANDIDATES + ', mark@example.com' });
  assert.equal(roleOf('mark@example.com', roster), 'operator');
});

// ── Whose data ──────────────────────────────────────────────────────────────

test('a candidate always sees themselves, whatever desk_as says', async () => {
  const roster = await rosterFromEnv(ENV);
  const bea = roster.candidates.find((c) => c.email === 'bea@example.com');
  // The forged cookie names another real candidate. It must be ignored.
  const got = resolveCandidate('ann@example.com', roster, bea.id);
  assert.equal(got.email, 'ann@example.com');
});

test('an operator sees the candidate they picked, if that candidate is on the roster', async () => {
  const roster = await rosterFromEnv(ENV);
  const bea = roster.candidates.find((c) => c.email === 'bea@example.com');
  assert.equal(resolveCandidate('mark@example.com', roster, bea.id).email, 'bea@example.com');
  // An id that is not on the roster buys nothing.
  assert.equal(resolveCandidate('mark@example.com', roster, 'ffffffffffffffff'), null,
    'two candidates and a bogus pick must resolve to NOBODY, not to a default');
  assert.equal(resolveCandidate('mark@example.com', roster, ''), null, 'no pick, two candidates: ask');
});

test('an operator with exactly one candidate on the roster gets them without picking', async () => {
  const roster = await rosterFromEnv({ ...ENV, DESK_CANDIDATES: 'ann@example.com' });
  assert.equal(resolveCandidate('mark@example.com', roster, '').email, 'ann@example.com');
});

test('an operator who is also a candidate defaults to themselves', async () => {
  const roster = await rosterFromEnv({ ...ENV, DESK_CANDIDATES: ENV.DESK_CANDIDATES + ', mark@example.com' });
  assert.equal(resolveCandidate('mark@example.com', roster, '').email, 'mark@example.com');
});

test('someone on neither list resolves to nothing, even with a valid-looking pick', async () => {
  const roster = await rosterFromEnv(ENV);
  assert.equal(resolveCandidate('stranger@example.com', roster, roster.candidates[0].id), null);
});

// ── Blob paths ──────────────────────────────────────────────────────────────

test('every blob path is under c/<id>/ and cannot climb out', async () => {
  const id = await candidateId('ann@example.com');
  assert.equal(blobPath(id, 'desk-data-live.json'), `${PREFIX_ROOT}${id}/desk-data-live.json`);
  assert.equal(blobPath(id, 'onboard/abc.json'), `c/${id}/onboard/abc.json`);
  assert.throws(() => blobPath('not-an-id', 'x.json'), /bad candidate id/);
  assert.throws(() => blobPath(id + 'ff', 'x.json'), /bad candidate id/, 'wrong length');
  assert.throws(() => blobPath(id.toUpperCase(), 'x.json'), /bad candidate id/, 'must be lowercase hex');
  assert.throws(() => blobPath(id, '../status.json'), /bad blob name/);
  assert.throws(() => blobPath(id, '/status.json'), /bad blob name/);
  assert.throws(() => blobPath(id, ''), /bad blob name/);
});

// ── identify(): the whole gate in one call ──────────────────────────────────

async function jar(email, as) {
  const parts = [`${SESSION_COOKIE}=${await signSession(ENV.DESK_SESSION_SECRET, email)}`];
  if (as) parts.push(`${AS_COOKIE}=${as}`);
  return parts.join('; ');
}

test('identify: a signed-in candidate is themselves', async () => {
  const who = await identify({ cookieHeader: await jar('ann@example.com'), env: ENV });
  assert.equal(who.role, 'candidate');
  assert.equal(who.candidate.email, 'ann@example.com');
});

test('identify: a valid session for an address no longer on the roster is refused', async () => {
  // This is why the address lives inside the cookie: removal takes effect now,
  // not at the end of a thirty-day session.
  const cookie = await jar('bea@example.com');
  assert.ok(await identify({ cookieHeader: cookie, env: ENV }), 'sanity: bea is on the roster');
  const without = { ...ENV, DESK_CANDIDATES: 'ann@example.com' };
  assert.equal(await identify({ cookieHeader: cookie, env: without }), null);
});

test('identify: no session, wrong secret, or a forged cookie is nobody', async () => {
  assert.equal(await identify({ cookieHeader: '', env: ENV }), null);
  const cookie = await jar('ann@example.com');
  assert.equal(await identify({ cookieHeader: cookie, env: { ...ENV, DESK_SESSION_SECRET: 'other' } }), null);
  assert.equal(await identify({ cookieHeader: cookie, env: { ...ENV, DESK_SESSION_SECRET: '' } }), null,
    'a missing secret closes the door rather than opening it');
});

test('identify: the operator picks; the candidate cannot', async () => {
  const roster = await rosterFromEnv(ENV);
  const bea = roster.candidates.find((c) => c.email === 'bea@example.com');
  const op = await identify({ cookieHeader: await jar('mark@example.com', bea.id), env: ENV });
  assert.equal(op.role, 'operator');
  assert.equal(op.candidate.email, 'bea@example.com');
  assert.equal(op.roster.candidates.length, 2, 'operators get the roster to pick from');

  const cand = await identify({ cookieHeader: await jar('ann@example.com', bea.id), env: ENV });
  assert.equal(cand.candidate.email, 'ann@example.com', 'a candidate with a forged desk_as is still themselves');
});
