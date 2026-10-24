# CEO AI Research Scraper

A small, local-only research tool for collecting publicly accessible CEO interviews and reporting about AI. It deliberately has no LLMs, agents, login, cloud services, or paywall bypasses.

## What it does

- Searches public YouTube results for CEO/company AI interviews and attempts to retrieve English captions when available.
- Searches Google News RSS for WSJ, Bloomberg, The New York Times, The Economist, and Forbes results, then extracts only text available on the resulting public page.
- Saves de-duplicated URLs and extracted content to `data/research.db` (SQLite).
- Shows source notices for rate limits, inaccessible pages, paywalls, and unavailable transcripts instead of inventing data.
- Exports the visible company’s saved records to CSV.

## Run locally

Use two terminals from the project root.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn backend.main:app --reload
```

```bash
cd frontend
npm install
npm run dev
```

Open the URL Vite prints (normally `http://localhost:5173`). The FastAPI API is at `http://127.0.0.1:8000`; API documentation is at `/docs`.

## Notes and limits

This is an MVP, not a comprehensive news archive. Publisher pages may block automated fetching, require a subscription, render client-side, or rate-limit requests. In those cases the app records a source notice and omits inaccessible article text. YouTube search depends on its public results-page format, and captions are included only when YouTube makes them available.

The scraper design is deliberately modular: add source implementations in `backend/scraper/`, then call them from `backend/main.py`. A future extraction or classification layer can run after this collection step without changing the saved-source schema.
