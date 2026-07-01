# 📰 AI News

Daily AI news aggregator covering Anthropic, OpenAI, and Gemini/DeepMind.

A static timeline site deployed on **Cloudflare Pages** — zero backend, zero build step.

## Structure

```
├── index.html          # Main timeline entry
├── Archive/            # Daily HTML reports (YYYY-MM-DD.html)
│   ├── 2026-07-01.html
│   └── ...
├── wrangler.toml       # Cloudflare Pages config
└── package.json
```

## Deploy

```bash
npm run deploy
```

### First deploy

```bash
npx wrangler pages deploy . --project-name ai-news
```

Then set a custom domain in the Cloudflare Dashboard.

## Dev

```bash
npm run dev        # local preview with live-reload
```
