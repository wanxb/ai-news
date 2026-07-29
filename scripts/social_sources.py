"""Best-effort collectors for publicly accessible social media posts."""

from __future__ import annotations

import base64
import html
import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

import requests


BEIJING_TZ = ZoneInfo("Asia/Shanghai")
X_TIMEOUT = 15
X_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.8",
}

Article = Dict[str, str]
LogFn = Callable[[str], None]
RequestFn = Callable[..., requests.Response]


@dataclass(frozen=True)
class XAccount:
    source: str
    handle: str


# These are official public accounts for the international companies already
# tracked by the site. They are intentionally code-owned: collection requires
# no user configuration, cookies, API tokens, or login state.
PUBLIC_X_ACCOUNTS = (
    XAccount("Anthropic", "AnthropicAI"),
    XAccount("OpenAI", "OpenAI"),
    XAccount("Gemini", "GoogleDeepMind"),
    XAccount("Gemini", "GoogleAI"),
    XAccount("Gemini", "GeminiApp"),
    XAccount("Meta AI", "AIatMeta"),
    XAccount("Microsoft AI", "MicrosoftAI"),
    XAccount("Mistral AI", "MistralAI"),
    XAccount("Hugging Face", "huggingface"),
    XAccount("NVIDIA AI", "NVIDIAAI"),
    XAccount("xAI", "xai"),
)


def _extract_record(page: str, marker: str) -> str:
    """Return a brace-delimited object assigned after marker."""
    marker_pos = page.find(marker)
    if marker_pos < 0:
        return ""

    start = page.find("{", marker_pos + len(marker))
    if start < 0:
        return ""

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(page)):
        char = page[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return page[start:index + 1]

    return ""


def _decode_js_string(raw: str) -> str:
    """Decode the JSON-compatible strings embedded in X's initial data."""
    normalized = raw.replace("\\'", "'")
    try:
        return html.unescape(json.loads(f'"{normalized}"'))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return html.unescape(
            normalized.replace("\\n", "\n").replace("\\r", "").replace("\\\"", '"')
        )


def _string_field(record: str, field: str) -> str:
    match = re.search(
        rf'{re.escape(field)}:"((?:\\.|[^"\\])*)"',
        record,
    )
    return _decode_js_string(match.group(1)) if match else ""


def _is_original_post(page: str, encoded_id: str, text: str) -> bool:
    if text.lstrip().startswith("RT @"):
        return False

    tweet_record = _extract_record(page, f'"{encoded_id}":$R[')
    if "reply_to_results:" in tweet_record and "reply_to_results:null" not in tweet_record:
        return False

    legacy_record = _extract_record(page, f'"client:{encoded_id}:legacy":$R[')
    if (
        "retweeted_status_results:" in legacy_record
        and "retweeted_status_results:null" not in legacy_record
    ):
        return False

    return True


def parse_x_profile(page: str, source: str, handle: str) -> List[Article]:
    """Parse recent original posts from an anonymous public X profile page."""
    timeline_ids = list(
        dict.fromkeys(re.findall(r"TimelineTimelineEntry:tweet-(\d+)", page))
    )
    articles: List[Article] = []

    for post_id in timeline_ids:
        encoded_id = base64.b64encode(f"Tweet:{post_id}".encode("ascii")).decode("ascii")
        details = _extract_record(page, f'"client:{encoded_id}:details":$R[')
        if not details:
            continue

        text = _string_field(details, "full_text").strip()
        created_match = re.search(r"created_at_ms:(\d+)", details)
        if not text or not created_match or not _is_original_post(page, encoded_id, text):
            continue

        created_at = datetime.fromtimestamp(
            int(created_match.group(1)) / 1000,
            tz=timezone.utc,
        ).astimezone(BEIJING_TZ)
        compact_text = re.sub(r"\s+", " ", text).strip()
        if len(compact_text) > 500:
            compact_text = compact_text[:497].rstrip() + "..."

        articles.append({
            "title": compact_text,
            "url": f"https://x.com/{handle}/status/{post_id}",
            "date": created_at.date().isoformat(),
            "source": source,
            "summary": "",
            "source_type": "social",
            "platform": "X",
            "account": f"@{handle}",
            "external_id": post_id,
        })

    return articles


def fetch_x_account(
    account: XAccount,
    request_get: RequestFn = requests.get,
    timeout: int = X_TIMEOUT,
) -> List[Article]:
    """Fetch one public X profile without credentials."""
    response = request_get(
        f"https://x.com/{account.handle}",
        headers=X_HEADERS,
        timeout=timeout,
    )
    response.raise_for_status()
    return parse_x_profile(response.text, account.source, account.handle)


def fetch_public_x_posts(
    log: Optional[LogFn] = None,
    request_get: RequestFn = requests.get,
) -> List[Article]:
    """Fetch all built-in public X accounts, isolating per-account failures."""
    logger = log or (lambda _message: None)

    def fetch_one(account: XAccount) -> List[Article]:
        try:
            posts = fetch_x_account(account, request_get=request_get)
            if posts:
                logger(f"  OK X @{account.handle}: {len(posts)} public posts")
            else:
                logger(f"  WARN X @{account.handle}: no anonymous timeline data")
            return posts
        except requests.RequestException as exc:
            logger(f"  WARN X @{account.handle}: public request failed ({exc})")
        except Exception as exc:
            logger(f"  WARN X @{account.handle}: parse failed ({exc})")
        return []

    articles: List[Article] = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        for posts in pool.map(fetch_one, PUBLIC_X_ACCOUNTS):
            articles.extend(posts)
    return articles
