# 📰 AI News

Daily AI news aggregator covering **Anthropic**, **OpenAI**, and **Google Gemini/DeepMind**.

A static timeline site deployed on **Cloudflare Pages** — zero backend, zero build step.
Auto-scraped and updated every day at 07:00 via **GitHub Actions**.

## Structure

```
├── index.html               # Timeline entry point
├── Archive/                  # Daily HTML reports (YYYY-MM-DD.html)
│   ├── 2026-07-01.html
│   └── ...
├── scripts/
│   ├── fetch_news.py        # News scraper & report generator
│   └── requirements.txt     # Python dependencies
├── .github/workflows/
│   └── daily-news.yml       # Scheduled scraper + deploy pipeline
├── wrangler.toml            # Cloudflare Pages config
└── package.json
```

## How it works

1. **GitHub Actions** triggers daily at 07:00 China time (23:00 UTC previous day)
2. **`fetch_news.py`** scrapes 4 sources:
   - **Anthropic** → HTML scrape of `anthropic.com/news` (no official RSS)
   - **OpenAI** → RSS `openai.com/news/rss.xml`
   - **Google AI** → RSS `blog.google/technology/ai/rss/` + `blog.google/innovation-and-ai/rss/`
   - **DeepMind** → RSS `deepmind.google/blog/rss.xml`
3. Filters by coverage window (Mon = catch up weekends; other days = last 1–2 days)
4. Deduplicates against `Archive/.seen-urls.txt`
5. Generates `Archive/YYYY-MM-DD.html` and updates `index.html` timeline
6. Commits changes and auto-deploys to **Cloudflare Pages**

## Quick Start

### 1. Local testing

```bash
pip install -r scripts/requirements.txt

# Run for today
python scripts/fetch_news.py

# Dry run (preview without writing)
python scripts/fetch_news.py --dry-run

# Run for a specific date
python scripts/fetch_news.py --date 2026-07-01
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

> **Note:** The scraper uses RSS feeds and public HTTP requests — no API keys needed
> for news fetching. If a source returns 403 (OpenAI sometimes does), it's skipped
> gracefully — remaining sources still get processed.

## Coverage Window

| Run day | Looks back |
|---------|-----------|
| Monday  | Saturday – Monday (catch up weekends) |
| Tue–Sat | Previous day – today (1 day) |
| Sunday  | Saturday – Sunday (2 days) |
