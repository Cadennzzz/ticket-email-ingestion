# ticket-email-ingestion

Scans a Gmail inbox for ticket-marketplace order emails, extracts structured
transaction data with an LLM, and stores it in a local SQLite database
(`transactions.db`).

## Automated ingestion

A GitHub Actions workflow (`.github/workflows/ingest.yml`) runs `ingest.py`
automatically once a day at 09:00 UTC, and can also be triggered manually.

### 1. Add the repo secrets

The workflow needs the same three environment variables `ingest.py` reads
from `.env` locally, but as GitHub Actions secrets — `.env` itself is
gitignored and never committed, so these have to be configured separately:

1. Go to **Settings → Secrets and variables → Actions → New repository secret**
   in this repo on GitHub.
2. Add each of the following as its own secret:
   - `GMAIL_USER`
   - `GMAIL_APP_PASSWORD`
   - `GEMINI_API_KEY`

### 2. How the daily run updates the repo

Each run scans the inbox, extracts any new ticket transactions, and — if
`transactions.db` changed — commits and pushes it back to the repo with a
message like `chore: ingest run 2026-09-19 (3 new transactions)`. If nothing
new was found, no commit is made.

Because `transactions.db` is committed by the workflow, pulling the repo
locally (`git pull`) will bring in any new rows from the daily run.

If `ingest.py` itself fails (bad credentials, API error, etc.), the workflow
fails loudly — you'll see a red X on the run in the **Actions** tab — instead
of silently doing nothing.

### 3. Triggering a manual run

Go to the **Actions** tab, select the **Ingest ticket emails** workflow in
the sidebar, and click **Run workflow**.
