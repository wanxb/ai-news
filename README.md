# 📰 AI News

Daily AI news aggregator covering major international and Chinese AI companies.

A static timeline site deployed on **Cloudflare Pages** — zero backend, zero build step.
Auto-scraped and updated every day at 07:00 Beijing time via **GitHub Actions**.

## Structure

```
├── index.html               # Timeline entry point
├── Archive/                  # Daily HTML reports (YYYY-MM-DD.html)
│   ├── 2026-07-01.html
│   └── ...
├── scripts/
│   ├── fetch_news.py        # News scraper & report generator
│   ├── social_sources.py    # Anonymous public social-media collectors
│   ├── sources.json         # Official website/feed source definitions
│   ├── tests/               # Offline parser and report tests
│   └── requirements.txt     # Python dependencies
├── .github/workflows/
│   └── daily-news.yml       # Scheduled scraper + deploy pipeline
├── wrangler.toml            # Cloudflare Pages config
└── package.json
```

## How it works

1. **GitHub Actions** triggers daily at 07:00 China time (23:00 UTC previous day)
2. **`fetch_news.py`** collects configured official sites and RSS/Atom feeds.
3. **`social_sources.py`** anonymously reads recent public X profile data for the
   official international company accounts. It uses no API token, cookie, or login.
4. Filters by coverage window (Mon = catch up weekends; other days = last 1–2 days).
5. Removes replies, reposts, previously seen URLs, and duplicate URLs in the same run.
6. Generates `Archive/YYYY-MM-DD.html` with website and X items grouped by company,
   then updates the `index.html` timeline.
7. Commits changes and auto-deploys to **Cloudflare Pages**.

## Quick Start

### 1. Local testing

```bash
pip install -r scripts/requirements.txt

# Run for today in Beijing
python scripts/fetch_news.py

# Dry run (preview without writing)
python scripts/fetch_news.py --dry-run

# Run for a specific date
python scripts/fetch_news.py --date 2026-07-01

# Run the offline parser and report tests
python -m unittest discover -s scripts/tests -v
```

### 2. Local preview

```bash
npm install
npm run dev
```
Opens a local dev server with live-reload at `localhost:8788`.

### 3. Deploy to Cloudflare Pages

```bash
# First deployment (creates the project)
npx wrangler pages deploy . --project-name ai-news

# Subsequent deployments
npm run deploy
```

After first deploy, you'll get a `https://ai-news.pages.dev` URL.
To use a custom domain, uncomment the `routes` section in `wrangler.toml`.

### 4. Set up GitHub Actions automation

| Step | Action |
|---|---|
| 1 | Push this repo to GitHub |
| 2 | In repo **Settings → Secrets and variables → Actions**, add: |
| | `CLOUDFLARE_API_TOKEN` — Cloudflare API token with Pages write permission |
| | `CLOUDFLARE_ACCOUNT_ID` — your Cloudflare account ID |
| 3 | The workflow runs automatically every day at 07:00 China time |
| 4 | Or trigger manually: **Actions → Daily AI News → Run workflow** |

> **Note:** News and social collection use public HTTP requests only; no collection
> API keys are needed. X can change or restrict its anonymous profile response at any
> time. An unavailable account is skipped without blocking other sources or the daily
> report. The Cloudflare secrets above are used only for deployment.

## Coverage Window

| Run day | Looks back |
|---------|-----------|
| Monday  | Saturday – Monday (catch up weekends) |
| Tue–Sat | Previous day – today (1 day) |
| Sunday  | Saturday – Sunday (2 days) |
