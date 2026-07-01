#!/usr/bin/env python3
"""
AI News Aggregator — daily news scraper & HTML report generator.

Fetches the latest AI news from Anthropic, OpenAI, Google AI, and DeepMind,
generates an Archive/YYYY-MM-DD.html report, deduplicates via .seen-urls.txt,
and updates index.html with the latest timeline.

Usage:
    python scripts/fetch_news.py                     # run for today
    python scripts/fetch_news.py --date 2026-07-01   # run for a specific date
    python scripts/fetch_news.py --dry-run            # preview without writing
"""

from __future__ import annotations

import argparse
import html as html_mod
import json
import os
import re
import sys
from datetime import datetime, timedelta, date
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup

# ── Paths ──────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
ARCHIVE_DIR = BASE_DIR / "Archive"
INDEX_FILE = BASE_DIR / "index.html"
SEEN_URLS_FILE = ARCHIVE_DIR / ".seen-urls.txt"
SOURCES_FILE = BASE_DIR / "scripts" / "sources.json"

# Ensure Archive/ exists
ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
SEEN_URLS_FILE.parent.mkdir(parents=True, exist_ok=True)

# ── Source Config ───────────────────────────────────────────────────────────

def load_sources() -> dict:
    """Load and return the sources.json config."""
    if not SOURCES_FILE.exists():
        log(f"  ✗ sources.json not found at {SOURCES_FILE}")
        sys.exit(1)
    try:
        cfg = json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
        return cfg
    except json.JSONDecodeError as e:
        log(f"  ✗ Invalid sources.json: {e}")
        sys.exit(1)


def get_source_map(cfg: dict) -> dict:
    """Build a {source_name -> source_config} lookup from the config."""
    return {s["name"]: s for s in cfg["sources"] if s.get("enabled", True)}


def generate_badge_css(cfg: dict) -> str:
    """Generate CSS for all source badges (matched to 'badge {id}' class usage)."""
    lines = []
    for s in cfg["sources"]:
        if s.get("enabled", True):
            lines.append(f'  .{s["id"]} {{ background: {s["badge_color"]}; }}')
    return "\n".join(lines)


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}
TIMEOUT = 30  # seconds

Article = Dict[str, str]  # {title, url, date, source, summary}


# ── Helpers ─────────────────────────────────────────────────────────────────

def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", file=sys.stderr)


def http_get(url: str) -> Optional[requests.Response]:
    """Safe HTTP GET — returns None on failure."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp
    except requests.RequestException as e:
        log(f"  ✗ HTTP error fetching {url}: {e}")
        return None


def parse_date(date_str: str) -> Optional[date]:
    """Try to parse a date string in various formats."""
    fmts = [
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%d %b %Y",
        "%d %B %Y",
        "%B %d, %Y",
        "%b %d, %Y",
        "%a, %d %b %Y %H:%M:%S %z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%SZ",
    ]
    for fmt in fmts:
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
            return dt.date()
        except ValueError:
            continue
    # Fallback: email.utils for RFC 2822 dates (common in RSS)
    try:
        dt = parsedate_to_datetime(date_str.strip())
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return dt.date()
    except (ValueError, TypeError):
        pass
    return None


def date_range_for(today: date) -> Tuple[date, date]:
    """
    Determine coverage window.

    Runs daily at 07:00 China time, covering the previous calendar day.
    - Monday: covers Sat+Sun+Mon (weekend catch-up since Fri 23:00 UTC run
      covers Sat morning, but Sat/Sun runs cover the full weekend)
    - All other days: covers yesterday → today (1 day window)
    """
    wd = today.weekday()  # Mon=0, Sun=6
    if wd == 0:  # Monday — catch up any Friday/Saturday items missed by weekend runs
        start = today - timedelta(days=3)
    elif wd == 6:  # Sunday — catch up Saturday too
        start = today - timedelta(days=2)
    else:
        start = today - timedelta(days=1)
    return start, today


# ── Dedup ───────────────────────────────────────────────────────────────────

def load_seen_urls() -> set:
    """Load previously seen URLs from .seen-urls.txt."""
    if SEEN_URLS_FILE.exists():
        raw = SEEN_URLS_FILE.read_text(encoding="utf-8").strip()
        return {line.strip() for line in raw.splitlines() if line.strip()}
    return set()


def save_seen_urls(urls: set) -> None:
    """Write dedup record (sorted)."""
    SEEN_URLS_FILE.write_text(
        "\n".join(sorted(urls)) + "\n",
        encoding="utf-8",
    )
    log(f"  ✓ Updated dedup file ({len(urls)} total URLs)")


# ── Feed Parsers ────────────────────────────────────────────────────────────

def parse_rss_feed(url: str, source_name: str) -> List[Article]:
    """Parse an RSS or Atom feed into articles.

    Handles both RSS 2.0 (<item>) and Atom (<entry>) formats.
    """
    log(f"  → Fetching RSS feed: {url}")
    resp = http_get(url)
    if resp is None:
        return []

    articles: List[Article] = []
    try:
        root = ElementTree.fromstring(resp.content)
    except ElementTree.ParseError as e:
        log(f"  ✗ XML parse error: {e}")
        # Fall back to BeautifulSoup for malformed feeds
        soup = BeautifulSoup(resp.content, "lxml-xml")
        root = soup.find("rss") or soup.find("feed")
        if root is None:
            return []
        return _parse_feed_soup(root, source_name)

    # Strip namespace — ElementTree includes it in tag names
    items = root.findall(".//item") or root.findall(".//entry")
    if not items:
        # Try with namespace wildcards
        for elem in root.iter():
            tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if tag in ("item", "entry"):
                items.append(elem)
                break

    for item in items:
        article = _parse_feed_item(item, source_name)
        if article:
            articles.append(article)

    return articles


def _parse_feed_soup(soup: BeautifulSoup, source_name: str) -> List[Article]:
    """Fallback: parse feed using BeautifulSoup."""
    articles: List[Article] = []
    for item in soup.find_all("item") or soup.find_all("entry"):
        title_tag = item.find("title")
        link_tag = item.find("link")
        date_tag = item.find("pubDate") or item.find("published") or item.find("updated")
        desc_tag = item.find("description") or item.find("summary")

        title = title_tag.text.strip() if title_tag else ""
        url = link_tag.text.strip() if link_tag and link_tag.text else ""
        if not url and link_tag and link_tag.get("href"):
            url = link_tag["href"].strip()
        pub_date = date_tag.text.strip() if date_tag else ""
        summary = ""
        if desc_tag:
            # Strip HTML tags from description
            summary = BeautifulSoup(desc_tag.text, "html.parser").get_text(separator=" ", strip=True)
            if len(summary) > 200:
                summary = summary[:200].rsplit(" ", 1)[0] + "…"

        if not title or not url:
            continue

        articles.append({
            "title": title,
            "url": url,
            "date": pub_date,
            "source": source_name,
            "summary": summary,
        })
    return articles


def _parse_feed_item(item, source_name: str) -> Optional[Article]:
    """Parse a single RSS item or Atom entry from ElementTree node."""
    def _tag(elem, name: str) -> Optional[str]:
        """Get text from a child tag, handling namespaces."""
        for child in elem.iter():
            t = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if t == name:
                return child.text.strip() if child.text else None
        return None

    title = _tag(item, "title")
    url = _tag(item, "link")
    pub_date = _tag(item, "pubDate") or _tag(item, "published") or _tag(item, "updated")
    desc = _tag(item, "description") or _tag(item, "summary")

    # Atom feeds put link in href attribute
    if not url:
        for child in item.iter():
            t = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if t == "link" and child.get("href"):
                url = child["href"].strip()
                break

    if not title or not url:
        return None

    summary = ""
    if desc:
        soup = BeautifulSoup(desc, "html.parser")
        summary = soup.get_text(separator=" ", strip=True)
        if len(summary) > 200:
            summary = summary[:200].rsplit(" ", 1)[0] + "…"

    return {
        "title": title,
        "url": url,
        "date": pub_date or "",
        "source": source_name,
        "summary": summary,
    }


# ── Generic Fetchers ────────────────────────────────────────────────────────

def fetch_rss_source(cfg_entry: dict) -> List[Article]:
    """Generic RSS fetcher — handles single rss_url or multiple rss_urls.

    Reads 'rss_url' (str) or 'rss_urls' (list) from the config entry.
    Falls back to HTML scrape if RSS fails and fallback_url is provided.
    """
    articles: List[Article] = []
    source_name = cfg_entry["name"]
    seen_urls: set = set()

    rss_urls = cfg_entry.get("rss_urls", [])
    if cfg_entry.get("rss_url"):
        rss_urls.append(cfg_entry["rss_url"])

    for url in rss_urls:
        fe = parse_rss_feed(url, source_name)
        for art in fe:
            if art["url"] not in seen_urls:
                seen_urls.add(art["url"])
                articles.append(art)
        if fe:
            log(f"  ✓ Found {len(fe)} articles from {url}")

    if articles:
        log(f"  ✓ Total: {len(articles)} unique articles from {source_name}")
        return articles

    # Fallback: HTML scrape
    fallback = cfg_entry.get("fallback_url") or cfg_entry.get("url")
    if fallback:
        log(f"  → RSS failed, scraping HTML: {fallback}")
        return _generic_scrape(fallback, source_name, cfg_entry.get("scrape_config", {}))

    return []


def fetch_scrape_source(cfg_entry: dict) -> List[Article]:
    """Generic HTML scrape fetcher."""
    return _generic_scrape(
        cfg_entry["url"],
        cfg_entry["name"],
        cfg_entry.get("scrape_config", {}),
    )


def _generic_scrape(url: str, source_name: str, scrape_config: dict) -> List[Article]:
    """Scrape a URL for article links.

    scrape_config can have:
      - link_pattern: str — only keep links whose href contains this string
      - base_url: str — prepend to relative links
      - selector: str — CSS selector override (default: looks for <a> tags)
    """
    articles: List[Article] = []
    pattern = scrape_config.get("link_pattern")
    base = scrape_config.get("base_url", "")

    resp = http_get(url)
    if resp is None:
        return []

    soup = BeautifulSoup(resp.content, "lxml")
    links = soup.select(scrape_config.get("selector", "a"))

    seen_titles = set()
    for a_tag in links:
        href = a_tag.get("href", "")
        title = a_tag.get_text(strip=True)

        # Apply link pattern filter
        if pattern and pattern not in href:
            continue
        if not href or not title or len(title) < 5:
            continue

        # Skip navigation/social links
        if href.startswith("#") or href.startswith("javascript:"):
            continue

        if title in seen_titles:
            continue
        seen_titles.add(title)

        # Make absolute URL
        if href.startswith("/"):
            href = base + href if base else href
        elif not href.startswith("http"):
            href = base + "/" + href if base else href

        articles.append({
            "title": title,
            "url": href,
            "date": "",
            "source": source_name,
            "summary": "",
        })

    if articles:
        log(f"  ✓ Scraped {len(articles)} links from {url}")
    else:
        log(f"  ✗ No links found at {url} (may be JS-rendered)")

    return articles


# ── Source Fetchers ─────────────────────────────────────────────────────────

def fetch_anthropic() -> List[Article]:
    """Fetch news from Anthropic.

    Anthropic has no working official RSS feed (feed.xml and rss.xml both
    return 404 / no response), so we scrape the /news page directly.
    """
    articles: List[Article] = []

    log("  → No official RSS — scraping HTML...")
    resp = http_get("https://www.anthropic.com/news")
    if resp is None:
        return []

    soup = BeautifulSoup(resp.content, "lxml")
    # Try common article card patterns
    for selector in [
        "article a[href*='/news/']",
        "[class*='card'] a[href*='/news/']",
        "a[href*='/news/']",
    ]:
        links = soup.select(selector)
        if links:
            break

    seen_titles = set()
    for a_tag in links:
        href = a_tag.get("href", "")
        if not href or "/news/" not in href:
            continue
        if href.startswith("/"):
            href = "https://www.anthropic.com" + href
        title = a_tag.get_text(strip=True)
        if not title or len(title) < 5 or title in seen_titles:
            continue
        seen_titles.add(title)
        articles.append({
            "title": title,
            "url": href,
            "date": "",
            "source": "Anthropic",
            "summary": "",
        })

    log(f"  ✓ Scraped {len(articles)} article links")
    return articles


def fetch_openai() -> List[Article]:
    """Fetch news from OpenAI.

    Confirmed working RSS: https://openai.com/news/rss.xml
    """
    articles = parse_rss_feed("https://openai.com/news/rss.xml", "OpenAI")
    if articles:
        log(f"  ✓ Found {len(articles)} articles from RSS")
        return articles

    # Fallback: scrape /news/ or /index/
    for page_url in ["https://openai.com/news/", "https://openai.com/index/"]:
        log(f"  → RSS failed, trying HTML scrape: {page_url}")
        resp = http_get(page_url)
        if resp is None:
            continue

        soup = BeautifulSoup(resp.content, "lxml")
        links = soup.select("a[href*='/index/']")
        if not links:
            links = soup.select("a[href*='/news/']")
        if not links:
            # Try any article-like link
            links = soup.select("article a, [class*='card'] a, [class*='post'] a")

        seen_titles = set()
        for a_tag in links:
            href = a_tag.get("href", "")
            if not href:
                continue
            # OpenAI uses relative paths starting with /index/...
            if href.startswith("/"):
                href = "https://openai.com" + href
            title = a_tag.get_text(strip=True)
            if not title or len(title) < 5 or title in seen_titles:
                continue
            seen_titles.add(title)
            articles.append({
                "title": title,
                "url": href,
                "date": "",
                "source": "OpenAI",
                "summary": "",
            })

        if articles:
            log(f"  ✓ Scraped {len(articles)} article links")
            break
        else:
            log("  ✗ No links found (may be JS-rendered)")

    return articles


def fetch_google_ai() -> List[Article]:
    """Fetch news from Google AI Blog.

    Confirmed working RSS feeds:
      - https://blog.google/technology/ai/rss/  (AI research & technology)
      - https://blog.google/innovation-and-ai/rss/  (broader AI products)
    """
    seen_urls: set = set()
    collected: List[Article] = []

    for feed_name, rss_url in [
        ("Gemini (Google AI tech)", "https://blog.google/technology/ai/rss/"),
        ("Gemini (Innovation & AI)", "https://blog.google/innovation-and-ai/rss/"),
    ]:
        articles = parse_rss_feed(rss_url, "Gemini")
        if articles:
            log(f"  ✓ Found {len(articles)} articles from {feed_name}")
            for art in articles:
                if art["url"] not in seen_urls:
                    seen_urls.add(art["url"])
                    collected.append(art)

    if collected:
        log(f"  ✓ Total: {len(collected)} unique articles across Google feeds")
        return collected

    # Fallback: scrape the main page
    log("  → RSS failed, scraping HTML...")
    resp = http_get("https://blog.google/technology/ai/")
    if resp is None:
        return []

    soup = BeautifulSoup(resp.content, "lxml")
    links = soup.select("a[href*='/technology/ai/']")
    seen_titles = set()
    for a_tag in links:
        href = a_tag.get("href", "")
        if not href:
            continue
        if href.startswith("/"):
            href = "https://blog.google" + href
        title = a_tag.get_text(strip=True)
        if not title or len(title) < 5 or title in seen_titles:
            continue
        seen_titles.add(title)
        articles.append({
            "title": title,
            "url": href,
            "date": "",
            "source": "Gemini",
            "summary": "",
        })

    log(f"  ✓ Scraped {len(articles)} article links")
    return articles


def fetch_deepmind() -> List[Article]:
    """Fetch news from DeepMind blog.

    Confirmed working RSS: https://deepmind.google/blog/rss.xml
    """
    articles = parse_rss_feed("https://deepmind.google/blog/rss.xml", "Gemini")
    if articles:
        log(f"  ✓ Found {len(articles)} articles from DeepMind RSS")
        return articles

    log("  → RSS failed, scraping HTML...")
    resp = http_get("https://deepmind.google/blog/")
    if resp is None:
        return []

    soup = BeautifulSoup(resp.content, "lxml")
    links = soup.select("a[href*='/blog/']")
    seen_titles = set()
    articles = []
    for a_tag in links:
        href = a_tag.get("href", "")
        if not href:
            continue
        if href.startswith("/"):
            href = "https://deepmind.google" + href
        title = a_tag.get_text(strip=True)
        if not title or len(title) < 5 or title in seen_titles:
            continue
        seen_titles.add(title)
        articles.append({
            "title": title,
            "url": href,
            "date": "",
            "source": "Gemini",
            "summary": "",
        })

    log(f"  ✓ Scraped {len(articles)} article links")
    return articles


# ── Article Filtering ───────────────────────────────────────────────────────

def enrich_articles(articles: List[Article]) -> List[Article]:
    """Try to fetch missing publication dates and summaries by visiting each URL."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def enrich_one(art: Article) -> Article:
        """Fetch metadata for one article. Returns art as-is on failure (skip silently)."""
        if art["date"] and art["summary"]:
            return art  # Already has all info (e.g., from RSS)
        try:
            resp = requests.get(art["url"], headers=HEADERS, timeout=8)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.content, "lxml")

            # Try to extract date
            if not art["date"]:
                for meta in soup.select("meta[property='article:published_time'], meta[name='pubdate'], time"):
                    content = meta.get("content", "") or meta.get("datetime", "") or meta.text.strip()
                    if content:
                        parsed = parse_date(content)
                        if parsed:
                            # Only format the date if it's not empty
                            art["date"] = parsed.strftime("%m-%d")
                            break

            # Try to extract description/summary
            if not art["summary"]:
                for meta in soup.select("meta[name='description'], meta[property='og:description']"):
                    desc = meta.get("content", "")
                    if desc:
                        art["summary"] = desc[:200].rsplit(" ", 1)[0] + "…" if len(desc) > 200 else desc
                        break
                if not art["summary"]:
                    # Try first paragraph
                    p = soup.select_one("article p, main p, .content p, .post-content p")
                    if p:
                        text = p.get_text(strip=True)
                        art["summary"] = text[:200].rsplit(" ", 1)[0] + "…" if len(text) > 200 else text
        except requests.RequestException:
            pass
        except Exception:
            pass
        return art

    with ThreadPoolExecutor(max_workers=8) as pool:
        enriched = list(pool.map(enrich_one, articles))

    return enriched


def filter_by_window(
    articles: List[Article],
    start: date,
    end: date,
    seen_urls: set,
) -> List[Article]:
    """Filter articles by coverage window and dedup.

    Returns only new articles within the date window.
    """
    fresh: List[Article] = []
    for art in articles:
        url = art["url"].rstrip("/")
        if url in seen_urls:
            continue

        # Check date if available
        if art["date"]:
            parsed = parse_date(art["date"])
            if parsed and (parsed < start or parsed > end):
                continue

        fresh.append(art)

    return fresh


# ── Report Generation ───────────────────────────────────────────────────────

def weekday_name(d: date) -> str:
    return ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][d.weekday()]


def month_abbr(d: date) -> str:
    return ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][d.month - 1]


def generate_report(today: date, articles: List[Article], start: date, end: date,
                    sources_cfg: dict) -> str:
    """Generate an HTML report string — dynamically handles all sources."""
    date_str = today.strftime("%Y-%m-%d")
    day_name = weekday_name(today)

    # Build badge lookup: source name -> (css_class, label)
    badge_map = {}
    for s in sources_cfg["sources"]:
        if s.get("enabled", True):
            badge_map[s["name"]] = (s["id"], s["label"])

    # Group articles by source name
    groups: Dict[str, List[Article]] = {}
    for art in articles:
        src = art["source"]
        if src not in groups:
            groups[src] = []
        groups[src].append(art)

    # Coverage window description
    if start == end:
        coverage_desc = f"{start.strftime('%b %d (%a)')}"
    elif (end - start).days == 1:
        coverage_desc = f"{start.strftime('%b %d')} – {end.strftime('%b %d (%a)')}"
    else:
        coverage_desc = f"{start.strftime('%b %d')} – {end.strftime('%b %d (%a)')}"

    def render_section(name: str, items: List[Article]) -> str:
        cls, label = badge_map.get(name, (name.lower().replace(" ", "-"), name))
        parts = [f'<div class="src">',
                 f'<h2><span class="badge {cls}">{label}</span></h2>']

        if not items:
            parts.append('<div class="item"><span class="none">No new posts within the coverage window.</span></div>')
        else:
            for art in items:
                date_display = art.get("date", "")
                if date_display and len(date_display) <= 6:
                    date_tag = f'<span class="date">{date_display}</span>'
                elif date_display:
                    parsed = parse_date(date_display)
                    if parsed:
                        date_tag = f'<span class="date">{parsed.strftime("%m-%d")}</span>'
                    else:
                        date_tag = ""
                else:
                    date_tag = ""

                summary = art.get("summary", "")
                summary_html = f'<div class="desc">{html_mod.escape(summary)}</div>' if summary else ""

                parts.append(
                    f'<div class="item">'
                    f'<a href="{html_mod.escape(art["url"])}" target="_blank">'
                    f'{html_mod.escape(art["title"])}{date_tag}</a>'
                    f'{summary_html}</div>'
                )

        parts.append('</div>')
        return "\n".join(parts)

    # Render sections in display_order from config
    sections = []
    for sid in sources_cfg.get("display_order", []):
        # Find which source name corresponds to this id
        for s in sources_cfg["sources"]:
            if s["id"] == sid and s.get("enabled", True) and s["name"] in groups:
                sections.append(render_section(s["name"], groups[s["name"]]))
                break
        else:
            continue

    # Also render any article sources not in the display order (edge case)
    rendered_names = set()
    for sec in sections:
        pass  # We'll just append extras below

    # Add sections for sources with articles but not in display_order
    rendered_ids = set()
    for sid in sources_cfg.get("display_order", []):
        for s in sources_cfg["sources"]:
            if s["id"] == sid and s.get("enabled", True):
                rendered_ids.add(s["name"])
                break

    for name, arts in groups.items():
        if name not in rendered_ids and name in badge_map:
            sections.append(render_section(name, arts))

    badge_css = generate_badge_css(sources_cfg)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI News {date_str}</title>
<style>
  body {{ font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif; background:#f5f6f8; color:#1f2329; margin:0; padding:32px 16px; line-height:1.6; }}
  .wrap {{ max-width:820px; margin:0 auto; }}
  h1 {{ font-size:26px; margin:0 0 4px; }}
  .src {{ background:#fff; border-radius:12px; padding:20px 24px; margin-bottom:20px; box-shadow:0 1px 4px rgba(0,0,0,.06); }}
  .src h2 {{ font-size:18px; margin:0 0 14px; display:flex; align-items:center; gap:8px; }}
  .badge {{ font-size:12px; font-weight:600; color:#fff; padding:2px 10px; border-radius:20px; }}
{badge_css}
  .item {{ padding:12px 0; border-top:1px solid #eef0f3; }}
  .item:first-of-type {{ border-top:none; }}
  .item a {{ color:#1a56db; text-decoration:none; font-weight:600; font-size:15.5px; }}
  .item a:hover {{ text-decoration:underline; }}
  .date {{ color:#9aa0aa; font-size:12.5px; margin-left:6px; }}
  .desc {{ color:#52575e; font-size:14px; margin-top:4px; }}
  .none {{ color:#9aa0aa; font-size:14px; }}
  footer {{ text-align:center; color:#aab0ba; font-size:12px; margin-top:24px; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>📰 AI News · {date_str} ({day_name})</h1>

{chr(10).join(sections)}

  <footer>Auto-generated by fetch_news.py · Updated daily at 07:00</footer>
</div>
</body>
</html>
"""
    return html


# ── Index.html Update ───────────────────────────────────────────────────────

def update_index_html(new_date: str) -> bool:
    """Update the DAYS array in index.html with the new date.

    Scans Archive/*.html files, sorts newest first, replaces the DAYS array.
    Returns True if modified.
    """
    if not INDEX_FILE.exists():
        log(f"  ✗ index.html not found at {INDEX_FILE}")
        return False

    # Find all report files in Archive/
    report_files = sorted(ARCHIVE_DIR.glob("*.html"))
    entries = []
    for f in report_files:
        fname = f.stem  # e.g., "2026-07-01"
        # Validate YYYY-MM-DD format
        if re.match(r"^\d{4}-\d{2}-\d{2}$", fname):
            entries.append((fname, f"Archive/{f.name}"))

    # Sort newest first
    entries.sort(key=lambda x: x[0], reverse=True)

    if not entries:
        log("  ✗ No report files found in Archive/")
        return False

    # Build the new DAYS array
    lines = ["  const DAYS = ["]
    for date_str, path in entries:
        lines.append(f'    {{ date: "{date_str}", path: "{path}" }},')
    lines.append("  ];")
    new_days_block = "\n".join(lines)

    # Read and replace in index.html
    content = INDEX_FILE.read_text(encoding="utf-8")

    # Match the DAYS array — from "const DAYS = [" to "];"
    pattern = r"const DAYS = \[.*?\];"
    match = re.search(pattern, content, re.DOTALL)
    if not match:
        log("  ✗ Could not find DAYS array in index.html")
        return False

    content = content.replace(match.group(0), new_days_block)
    INDEX_FILE.write_text(content, encoding="utf-8")
    log(f"  ✓ Updated DAYS array with {len(entries)} entries (newest: {entries[0][0]})")
    return True


def update_seen_urls(seen: set, new_articles: List[Article]) -> None:
    """Add new article URLs to the dedup record."""
    for art in new_articles:
        seen.add(art["url"].rstrip("/"))
    save_seen_urls(seen)


def update_index_css(cfg: dict) -> bool:
    """Update the badge CSS in index.html to match sources config."""
    if not INDEX_FILE.exists():
        log(f"  ✗ index.html not found at {INDEX_FILE}")
        return False

    content = INDEX_FILE.read_text(encoding="utf-8")

    # Generate badge CSS block (with leading newline to separate from previous rule)
    css_lines = ["\n"]
    for s in cfg["sources"]:
        if s.get("enabled", True):
            css_lines.append(f'  #viewer .{s["id"]} {{ background: {s["badge_color"]}; }}')
    new_css_block = "\n".join(css_lines)

    # Find and replace the badge CSS section in index.html
    # Match one or more consecutive #viewer .xxx { background: ...; } lines
    pattern = r"\s*#viewer \.\w+\s*\{\s*background:\s*#[0-9a-fA-F]+;\s*\}(?:\s*\n\s*#viewer \.\w+\s*\{\s*background:\s*#[0-9a-fA-F]+;\s*\})*"
    match = re.search(pattern, content)
    if match:
        content = content.replace(match.group(0), new_css_block)
        INDEX_FILE.write_text(content, encoding="utf-8")
        log(f"  ✓ Updated badge CSS in index.html ({len(cfg['sources'])} sources)")
        return True
    else:
        log("  ⚠ Could not find badge CSS pattern in index.html — CSS not updated")
        return False


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="AI News Aggregator — fetch, generate, and deploy daily news reports.",
    )
    parser.add_argument(
        "--date", type=str, default=None,
        help="Target date (YYYY-MM-DD). Default: today.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Scrape and show results without writing files.",
    )
    parser.add_argument(
        "--update-css-only", action="store_true",
        help="Only update badge CSS in index.html from sources.json, then exit.",
    )
    args = parser.parse_args()

    # Load source config
    cfg = load_sources()
    log(f"📋 Loaded {len([s for s in cfg['sources'] if s.get('enabled', True)])} enabled sources from sources.json")

    # CSS-only mode
    if args.update_css_only:
        update_index_css(cfg)
        return

    # Determine target date
    if args.date:
        today = date.fromisoformat(args.date)
    else:
        today = date.today()

    date_str = today.strftime("%Y-%m-%d")
    start, end = date_range_for(today)
    log(f"📅 Date: {date_str} ({weekday_name(today)})")
    log(f"📡 Coverage: {start} → {end}")

    # Load dedup
    seen_urls = load_seen_urls()
    log(f"📁 Seen URLs in record: {len(seen_urls)}")

    # Check if report already exists for today
    report_path = ARCHIVE_DIR / f"{date_str}.html"
    if report_path.exists() and not args.dry_run:
        log(f"⚠️  Report for {date_str} already exists — will overwrite")

    # ── Fetch from all sources ───────────────────────────────────────────
    log("")
    log("🔍 Fetching news...")
    all_articles: List[Article] = []

    # Custom fetcher map — sources that need special handling
    custom_fetchers = {
        "Anthropic": fetch_anthropic,
        "OpenAI": fetch_openai,
    }

    for s in cfg["sources"]:
        if not s.get("enabled", True):
            continue

        name = s["name"]
        method = s.get("fetch_method")

        log(f"  ── {name} ────────────────────────")
        try:
            if method == "custom" and name in custom_fetchers:
                articles = custom_fetchers[name]()
            elif method == "rss":
                articles = fetch_rss_source(s)
            elif method == "scrape":
                articles = fetch_scrape_source(s)
            else:
                log(f"  ⚠ Unknown fetch_method '{method}' for {name}, skipping")
                continue

            log(f"  → Raw articles fetched: {len(articles)}")
            all_articles.extend(articles)
        except Exception as e:
            log(f"  ✗ Error fetching {name}: {e}")

    # ── Filter by window + dedup FIRST ────────────────────────────────────
    # Do this before enrichment to avoid enriching articles we don't need
    log("")
    log("🔎 Filtering new articles...")
    window_articles = filter_by_window(all_articles, start, end, seen_urls)
    log(f"  → Articles within coverage window: {len(window_articles)}")

    # ── Enrich (get dates & summaries) only for filtered articles ────────
    log("")
    log("📝 Enriching article metadata...")
    new_articles = enrich_articles(window_articles)
    log(f"  → Enriched {len(new_articles)} articles")

    # Show results
    for art in new_articles:
        date_info = f" [{art['date']}]" if art.get("date") else ""
        log(f"    • [{art['source']}]{date_info} {art['title']}")

    # ── Generate report ──────────────────────────────────────────────────
    log("")
    if not new_articles:
        log("📭 No new articles found — no report generated.")
        return

    if args.dry_run:
        log("🏁 Dry-run mode — files not written.")
        log(f"  Would generate: {report_path}")
        log(f"  Would add {len(new_articles)} URLs to dedup")
        return

    report_html = generate_report(today, new_articles, start, end, cfg)
    report_path.write_text(report_html, encoding="utf-8")
    log(f"✅ Report written: {report_path}")

    # ── Update dedup ─────────────────────────────────────────────────────
    update_seen_urls(seen_urls, new_articles)

    # ── Update index.html ────────────────────────────────────────────────
    update_index_html(date_str)
    update_index_css(cfg)

    # ── Summary ──────────────────────────────────────────────────────────
    log("")
    log("=" * 50)
    log("📊 SUMMARY")
    log(f"  Report: Archive/{date_str}.html")
    log(f"  Total new items: {len(new_articles)}")
    sources_found = set(art["source"] for art in new_articles)
    for s in sorted(sources_found):
        count = sum(1 for art in new_articles if art["source"] == s)
        log(f"    - {s}: {count}")
    log(f"  Timeline: index.html updated ({len(new_articles)})")
    log("=" * 50)


if __name__ == "__main__":
    main()
