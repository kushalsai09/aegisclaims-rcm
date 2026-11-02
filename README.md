# AEGISCLAIMS RCM

AEGISCLAIMS RCM is a provider-side healthcare revenue-cycle management prototype built for a MainStreet Family Care interview project. It ingests synthetic claims from local mock payer portals, analyzes denials against synthetic payer-policy documents, recommends corrections, creates appeal PDFs, records an audit trail, and reports revenue-cycle metrics.

This repository contains the authentic prototype source recovered from the original project workspace. It uses synthetic demonstration data only; it is not connected to real patients, payers, clearinghouses, or MainStreet Family Care systems.

## Implemented features

- FastAPI application and HTML/JavaScript operations dashboard
- SQLite claim and audit persistence through SQLAlchemy
- Playwright ingestion from four local mock payer portals
- ChromaDB policy retrieval over bundled synthetic policy documents
- OpenAI-assisted denial resolution when configured, with deterministic fallback logic
- Claim analysis, review, resubmission-state, batch-processing, and audit APIs
- Appeal PDF generation
- Denial, recovery, payer, and operational analytics
- Unit tests for the resolver, resolution pipeline, and analytics
- Optional Playwright demo-recording script

## Technology

Python, FastAPI, SQLAlchemy, SQLite, Pydantic, Playwright, ChromaDB, OpenAI, Jinja2, JavaScript, and ReportLab.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000>. The application creates its local SQLite and ChromaDB data stores on first run.

To enable model-assisted analysis, set `OPENAI_API_KEY` in `.env`. With no key, the resolver uses its deterministic fallback.

## Tests

```bash
pytest -q
```

## Project boundaries

- All claims, patients, policies, portals, and outcomes are synthetic.
- Portal submission and clearinghouse behavior are simulated.
- Suggested fixes and appeal documents require human review.
- The repository is a portfolio prototype, not a production medical billing system.
