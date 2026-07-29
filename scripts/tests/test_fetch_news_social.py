import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

from fetch_news import enrich_articles, filter_by_window, generate_report  # noqa: E402


def social_article(**overrides):
    article = {
        "title": "Public X post",
        "url": "https://x.com/OpenAI/status/1234567890123456789",
        "date": "2026-07-29",
        "source": "OpenAI",
        "summary": "",
        "source_type": "social",
        "platform": "X",
        "account": "@OpenAI",
        "external_id": "1234567890123456789",
    }
    article.update(overrides)
    return article


class SocialPipelineTests(unittest.TestCase):
    def test_social_posts_skip_page_enrichment(self):
        article = social_article()

        with patch("fetch_news.requests.get") as request_get:
            enriched = enrich_articles([article])

        request_get.assert_not_called()
        self.assertEqual(enriched, [article])

    def test_filter_deduplicates_posts_within_current_run(self):
        article = social_article()

        filtered = filter_by_window(
            [article, dict(article)],
            date(2026, 7, 28),
            date(2026, 7, 29),
            set(),
        )

        self.assertEqual(filtered, [article])

    def test_report_renders_social_metadata_and_escapes_content(self):
        article = social_article(title="New <model> & safety update")
        config = {
            "sources": [{
                "id": "openai",
                "name": "OpenAI",
                "label": "OpenAI",
                "badge_color": "#10a37f",
                "enabled": True,
            }],
            "display_order": ["openai"],
        }

        report = generate_report(
            date(2026, 7, 29),
            [article],
            date(2026, 7, 28),
            date(2026, 7, 29),
            config,
        )

        self.assertIn('<span class="platform x">X</span>', report)
        self.assertIn('<span class="account">@OpenAI</span>', report)
        self.assertIn("New &lt;model&gt; &amp; safety update", report)
        self.assertNotIn("New <model>", report)


if __name__ == "__main__":
    unittest.main()
