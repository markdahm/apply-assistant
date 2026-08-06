"""Substring matching on job text keeps costing real jobs.

Three separate places matched keywords with a bare ``in``:

* ``seniority_of()`` filed **Internal Auditor** and **International Tax Lead** as
  interns, because "intern" is a prefix of both.
* the exclude-keyword loops did the same, so an ``intern`` exclusion knocked out
  "Internal Audit Lead" — a title in the candidate's own field, since he is a
  certified ISO Internal Auditor.
* ``_emp_type()`` labelled a $87-100k Senior Auditor role as an **Internship**
  because its description mentioned internal audit.

This is the same shape as the "governance" dealbreaker eating every food-safety
posting, and the ``\\bus\\b`` trap in the remote filter. Table-driven, because
the value is in the cases you didn't think of.

    .venv/bin/python3 tests/test_keyword_boundaries.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apply_assistant.export_desk import _emp_type  # noqa: E402
from apply_assistant.knockout import keyword_hit, knockout, seniority_of  # noqa: E402

SENIORITY = [
    ("Internal Auditor", "mid", "the candidate's own certification"),
    ("ISO Internal Quality Auditor", "mid", ""),
    ("International Tax Reporting Lead", "senior", "'lead' wins, not 'intern'"),
    ("Internal Audit Data Analytics Lead", "senior", ""),
    ("Quality Assurance Intern", "intern", "a real internship still reads as one"),
    ("Summer Internship, Food Safety", "intern", ""),
    ("Interns Program Coordinator", "intern", "plural"),
    ("Director of Food Safety", "director", "unchanged"),
    ("FSQA Supervisor", "mid", "unchanged"),
]

KEYWORD = [
    ("intern", "internal auditor", False, "the bug"),
    ("intern", "international sales lead", False, ""),
    ("intern", "quality assurance intern", True, ""),
    ("sales", "wholesale quality manager", False, "'sales' inside 'wholesale'"),
    ("sales", "sales engineer", True, ""),
    ("ts/sci", "requires ts/sci clearance", True, "punctuated keyword still matches"),
    ("account executive", "senior account executive", True, "multi-word"),
    ("governance", "food safety governance program", True, "matches when genuinely present"),
]

EMP = [
    ("Senior Auditor, Food Safety", "leads internal audits for clients", "Full-time",
     "the $87-100k role mislabelled as an internship"),
    ("BGC Compliance Specialist", "internal controls and compliance", "Full-time", ""),
    ("QA Intern", "summer internship program", "Internship", "a real one"),
    ("Quality Technician", "supports contract manufacturing partners", "Full-time",
     "'contract manufacturing' is food-industry language, not an employment type"),
    ("Product Compliance Coordinator", "this is a contract role, 6 months", "Contract", ""),
    ("QA Technician", "part-time, 20 hours per week", "Part-time", ""),
]


def main():
    failed = 0

    for title, expected, note in SENIORITY:
        got = seniority_of(title)
        if got != expected:
            failed += 1
            print("  FAIL  seniority_of(%-38r) = %-9s expected %-9s %s" % (title, got, expected, note))

    for kw, text, expected, note in KEYWORD:
        got = keyword_hit(kw, text)
        if got != expected:
            failed += 1
            print("  FAIL  keyword_hit(%r, %r) = %s expected %s  %s" % (kw, text, got, expected, note))

    for title, desc, expected, note in EMP:
        got = _emp_type({"title": title, "description": desc})
        if got != expected:
            failed += 1
            print("  FAIL  _emp_type(%-34r) = %-11s expected %-11s %s" % (title, got, expected, note))

    # Integration: an Internal Auditor role must survive an "intern" exclusion.
    prof = {"preferences": {"exclude_role_keywords": ["intern"], "seniority_floor": "junior",
                            "seniority_ceiling": "director",
                            "target_role_keywords": ["internal auditor"], "locations": ["san jose"]}}
    row = {"title": "Internal Auditor", "location": "San Jose, CA", "remote": 0,
           "description": "ISO 9001 internal audits", "comp_max": None}
    ok, reasons = knockout(row, prof)
    if not ok:
        failed += 1
        print("  FAIL  integration: 'Internal Auditor' knocked out -> %s" % reasons)

    total = len(SENIORITY) + len(KEYWORD) + len(EMP) + 1
    print("%d passed, %d failed  (of %d)" % (total - failed, failed, total))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
