"""apply-assistant — a quality-over-spam job sourcing + application helper.

The engine pulls jobs from free public ATS feeds and Firecrawl-scraped boards,
normalizes and dedupes them, filters and scores each against a candidate
profile, and drafts a tailored resume + cover letter for the strong matches —
leaving the voice and the final "apply" click to the human.
"""

__version__ = "0.1.0"

# Load a local .env (if python-dotenv is installed and a .env file exists) so
# API keys can live in a file instead of the shell environment. Optional — a
# missing dependency or file is a no-op.
try:  # pragma: no cover - convenience only
    from dotenv import load_dotenv as _load_dotenv

    _load_dotenv()
except Exception:  # noqa: BLE001
    pass
