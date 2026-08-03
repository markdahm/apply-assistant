# profile/

The candidate's master profile. **Nothing real lives in git** — the actual
profile is ignored (see the root `.gitignore`); only templates are tracked.

To use the engine for a real person, create these files here from the
templates. They are the single source of truth the matcher, tailor, and
letter-writer read from:

| File | What it holds |
|------|---------------|
| `resume.md`          | The canonical resume — the **facts authority**. Tailoring may only re-word/re-order what's here; it never invents. |
| `preferences.md`     | Target roles, seniority, locations, comp floor, dealbreakers → drives the knockout filter + scoring. |
| `experience_bank.md` | Expanded bullets, quantified wins, references — extra true material the tailor/letters may draw on. |
| `voice_real.md`      | The person's real writing voice, distilled from letters/emails they actually wrote. The cover-letter layer is only as good as this. |

Start from `resume.example.md` and `config/profile.example.json`.

**Honesty rule (enforced in code):** every line in a tailored resume traces to
a real bullet in `resume.md`; every number in a cover letter must appear in the
fact sources. The validators fail closed — see `apply_assistant/tailor.py` and
`apply_assistant/letters.py`.
