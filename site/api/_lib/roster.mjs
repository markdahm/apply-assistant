// Who may sign in, what they may see, and where their data lives.
//
// The Desk serves several candidates from ONE deployment and ONE blob store.
// Every blob pathname is therefore prefixed by the candidate it belongs to, and
// every API handler resolves "which candidate" from the signed-in identity —
// never from a parameter the browser chose, except where an OPERATOR is choosing
// among candidates the roster already names.
//
// Two roles, two environment variables:
//
//   DESK_OPERATORS   — the people running the pipeline (Mark). See every
//                      candidate, switch between them.
//   DESK_CANDIDATES  — the people whose job search this is. See only their own.
//
// Both are comma/whitespace-separated email addresses, compared exactly after
// trimming and lowercasing. An address on neither list cannot sign in at all.
// An empty roster admits nobody: misconfigured means closed.
//
// The candidate id is the first 16 hex characters of sha256(lowercased email).
// It is stable, unguessable from the outside, safe in a pathname, and — the
// part that matters — computed IDENTICALLY by apply_assistant/tenant.py, which
// is how the pipeline on Mark's machine publishes into the right prefix.
// tests/test_candidate_id.py drives both implementations with the same inputs.

export const PREFIX_ROOT = 'c/';
export const ID_PATTERN = /^[a-f0-9]{16}$/;

export function normalizeEmail(email) {
  return String(email ?? '').trim().toLowerCase();
}

export function parseList(raw) {
  return String(raw ?? '')
    .split(/[,\s]+/)
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean);
}

export async function candidateId(email) {
  const e = normalizeEmail(email);
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(e));
  return Array.from(new Uint8Array(digest)).map((b) => b.toString(16).padStart(2, '0')).join('').slice(0, 16);
}

// { operators: [email], candidates: [{ email, id }] }
export async function rosterFromEnv(env) {
  const operators = parseList(env.DESK_OPERATORS);
  const emails = parseList(env.DESK_CANDIDATES);
  const candidates = [];
  for (const email of emails) candidates.push({ email, id: await candidateId(email) });
  return { operators, candidates };
}

// 'operator' | 'candidate' | null. Operator wins when an address is on both
// lists — Mark onboarding himself to test is the expected way that happens.
export function roleOf(email, roster) {
  const e = normalizeEmail(email);
  if (!e) return null;
  if (roster.operators.includes(e)) return 'operator';
  if (roster.candidates.some((c) => c.email === e)) return 'candidate';
  return null;
}

// Which candidate's data this person sees. Returns { email, id } or null.
//
//   candidate → their own entry, always. `asId` is ignored: a candidate cannot
//               choose to be someone else, whatever their cookie says.
//   operator  → the candidate named by `asId` if it is on the roster; failing
//               that, themselves if they are also a candidate; failing that,
//               the only candidate if there is exactly one; otherwise null,
//               which the UI renders as "pick a candidate".
export function resolveCandidate(email, roster, asId) {
  const role = roleOf(email, roster);
  if (!role) return null;
  const e = normalizeEmail(email);
  if (role === 'candidate') return roster.candidates.find((c) => c.email === e) || null;

  const wanted = String(asId ?? '').trim();
  if (wanted) {
    const hit = roster.candidates.find((c) => c.id === wanted);
    if (hit) return hit;
  }
  const self = roster.candidates.find((c) => c.email === e);
  if (self) return self;
  if (roster.candidates.length === 1) return roster.candidates[0];
  return null;
}

// `c/<id>/<name>`. The id is validated so a handler can never be talked into
// building a path outside a candidate's prefix, and the name may not climb.
export function blobPath(id, name) {
  if (!ID_PATTERN.test(String(id))) throw new Error('bad candidate id');
  const n = String(name ?? '');
  if (!n || n.startsWith('/') || n.includes('..')) throw new Error('bad blob name');
  return PREFIX_ROOT + id + '/' + n;
}
