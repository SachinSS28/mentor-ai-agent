from datetime import UTC, datetime
from types import SimpleNamespace

from mentor.briefing.digest import parse_scores
from mentor.briefing.sources import (
    FilteredFeed,
    abstract_text,
    clean,
    feed_items,
    is_past_event,
    load_config,
)

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>Feed</title>
<item><title>New N2O model for activated sludge</title><link>https://example.org/a</link>
<description>&lt;p&gt;Nitrous oxide &amp;amp; energy in WWTPs&lt;/p&gt;</description>
<pubDate>Sun, 18 Oct 2026 09:00:00 GMT</pubDate></item>
<item><title>Stock market update</title><link>https://example.org/b</link>
<description>Shares rose</description><pubDate>Sun, 18 Oct 2026 10:00:00 GMT</pubDate></item>
<item><title>Old drainage story</title><link>https://example.org/c</link>
<description>Drainage works</description><pubDate>Mon, 05 Oct 2026 10:00:00 GMT</pubDate></item>
</channel></rss>"""


class FakeClient:
    def get(self, url: str) -> SimpleNamespace:
        return SimpleNamespace(content=RSS, raise_for_status=lambda: None)


def test_feed_html_becomes_plain_text_cut_at_a_word() -> None:
    assert clean("<p>Water &amp;amp; <b>wastewater</b></p>") == "Water &amp; wastewater"
    assert clean("one two three four", limit=9) == "one two…"


def test_openalex_abstracts_are_rebuilt_in_order() -> None:
    assert abstract_text({"filtration": [1], "Riverbank": [0], "works": [2]}) == (
        "Riverbank filtration works"
    )
    assert abstract_text(None) is None


def test_feeds_keep_recent_items_and_apply_keywords() -> None:
    since = datetime(2026, 10, 16, tzinfo=UTC)
    every = feed_items("news", "Feed", "https://x", since, FakeClient())  # type: ignore[arg-type]
    assert [item.title for item in every] == [
        "New N2O model for activated sludge",
        "Stock market update",
    ]  # the 5 Oct story is older than `since`
    assert every[0].snippet == "Nitrous oxide & energy in WWTPs"  # XML, then HTML, unescaped
    filtered = FilteredFeed(url="https://x", keywords=["nitrous", "drainage"])
    kept = feed_items("news", "Feed", filtered, since, FakeClient())  # type: ignore[arg-type]
    assert [item.url for item in kept] == ["https://example.org/a"]


def test_the_real_briefing_config_loads() -> None:
    config = load_config()
    assert len(config.papers) == 5 and "Climate change & sustainability" in config.papers
    assert "Water Research" in config.journals
    hk = config.news["HKSAR Government press releases"]
    assert isinstance(hk, FilteredFeed) and "drainage" in hk.keywords


def test_score_lines_tolerate_formatting() -> None:
    reply = "Scores:\n1 | 85 | New N2O model\n* **#2** | 10% | Off-topic\njunk"
    assert parse_scores(reply) == [(1, 85, "New N2O model"), (2, 10, "Off-topic")]


def test_calls_and_conferences_naming_only_past_years_are_dropped() -> None:
    assert is_past_event("ONICWater 2020 : Optimization in Groundwater", 2026)
    assert not is_past_event("Water Week 2027 call for papers", 2026)
    assert not is_past_event("IWA World Water Congress 2025-2026", 2026)
    assert not is_past_event("Call for proposals: urban water research", 2026)
