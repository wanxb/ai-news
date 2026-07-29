import base64
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

from social_sources import parse_x_profile  # noqa: E402


def encoded_post_id(post_id: str) -> str:
    return base64.b64encode(f"Tweet:{post_id}".encode("ascii")).decode("ascii")


def profile_fixture(*, post_id: str, text: str, timestamp_ms: int, reply: bool = False) -> str:
    encoded = encoded_post_id(post_id)
    reply_value = '$R[9]={__ref:"TweetResults:1"}' if reply else "null"
    escaped_text = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return (
        '<script>'
        f'"{encoded}":$R[1]={{__typename:"Tweet",rest_id:"{post_id}",'
        f'reply_to_results:{reply_value}}},'
        f'"client:{encoded}:details":$R[2]={{__typename:"TBirdData",'
        f'created_at_ms:{timestamp_ms},full_text:"{escaped_text}"}},'
        f'"client:{encoded}:legacy":$R[3]={{__typename:"LegacyTweet",'
        'retweeted_status_results:null},'
        f'"urt:server:TimelineTimelineEntry:tweet-{post_id}":$R[4]={{}}'
        '</script>'
    )


class ParseXProfileTests(unittest.TestCase):
    def test_parses_public_original_post(self):
        timestamp_ms = int(
            datetime(2026, 7, 28, 23, 30, tzinfo=timezone.utc).timestamp() * 1000
        )
        page = profile_fixture(
            post_id="1234567890123456789",
            text='New model available now.\nRead the details at https://t.co/example',
            timestamp_ms=timestamp_ms,
        )

        articles = parse_x_profile(page, "OpenAI", "OpenAI")

        self.assertEqual(len(articles), 1)
        article = articles[0]
        self.assertEqual(article["date"], "2026-07-29")
        self.assertEqual(article["platform"], "X")
        self.assertEqual(article["account"], "@OpenAI")
        self.assertEqual(article["external_id"], "1234567890123456789")
        self.assertEqual(
            article["url"],
            "https://x.com/OpenAI/status/1234567890123456789",
        )
        self.assertNotIn("\n", article["title"])

    def test_filters_replies(self):
        page = profile_fixture(
            post_id="2234567890123456789",
            text="Thanks for the question.",
            timestamp_ms=1785285331000,
            reply=True,
        )

        self.assertEqual(parse_x_profile(page, "OpenAI", "OpenAI"), [])

    def test_filters_retweets(self):
        page = profile_fixture(
            post_id="3234567890123456789",
            text="RT @Example: Reposted announcement",
            timestamp_ms=1785285331000,
        )

        self.assertEqual(parse_x_profile(page, "OpenAI", "OpenAI"), [])

    def test_returns_empty_for_login_or_error_page(self):
        self.assertEqual(
            parse_x_profile("<html><title>Log in to X</title></html>", "OpenAI", "OpenAI"),
            [],
        )


if __name__ == "__main__":
    unittest.main()
