from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from mentor.db.models import Person
from mentor.network import desk, experts
from mentor.network.experts import Candidate, Paper
from mentor.network.store import normalize_status, parse_ref

HKT = ZoneInfo("Asia/Hong_Kong")
SINCE = date(2023, 1, 1)
POLYU = {"display_name": "Hong Kong Polytechnic University", "country_code": "HK"}
SHEFFIELD = {"display_name": "University of Sheffield", "country_code": "GB"}


def work(work_id: str, title: str, *authors: tuple[str, str, dict[str, str]]) -> dict[str, Any]:
    return {
        "id": f"https://openalex.org/{work_id}",
        "doi": f"https://doi.org/10.1/{work_id}",
        "title": title,
        "publication_year": 2026,
        "authorships": [
            {
                "author": {"id": f"https://openalex.org/{author_id}", "display_name": name},
                "institutions": [institution],
            }
            for author_id, name, institution in authors
        ],
    }


LEAKS = work(
    "W1",
    "Acoustic leak detection in water mains",
    ("A1", "Chan Tai Man", POLYU),
    ("A2", "Jane Smith", SHEFFIELD),
)
TRANSIENTS = work("W2", "Transient waves in pipe networks", ("A1", "Chan Tai Man", POLYU))


def test_each_phrase_part_is_matched_exactly() -> None:
    assert experts.phrase_query("riverbank filtration") == '"riverbank filtration"'
    assert experts.phrase_query("leak detection + water") == '"leak detection" "water"'
    assert experts.phrase_query('a, "b" + ') == '"a  b"'  # commas would split the filter


def test_only_authors_signing_from_hong_kong_are_candidates() -> None:
    found: dict[str, Candidate] = {}
    experts.add_works(found, [LEAKS, TRANSIENTS], "Water distribution")
    experts.add_works(found, [LEAKS], "AI in water")  # the same paper under a second topic
    assert list(found) == ["A1"]
    person = found["A1"]
    assert person.organization == "Hong Kong Polytechnic University"
    assert person.topics == {"Water distribution", "AI in water"}
    assert [p.title for p in person.papers] == [
        "Acoustic leak detection in water mains",
        "Transient waves in pipe networks",
    ]


def test_career_outside_hong_kong_needs_more_than_one_year() -> None:
    affiliations = [
        {"institution": POLYU, "years": [2026, 2025, 2024]},
        {"institution": SHEFFIELD, "years": [2015, 2014, 2013]},
        {"institution": {"display_name": "MIT", "country_code": "US"}, "years": [2018, 2017]},
        {"institution": {"display_name": "One Paper Uni", "country_code": "AU"}, "years": [2020]},
    ]
    assert experts.careers_abroad(affiliations) == ["University of Sheffield (GB)", "MIT (US)"]


def test_ranking_puts_international_careers_first_only_when_asked() -> None:
    local = Candidate("A1", "Local", "HKU", papers=[Paper("x", 2026, "u1"), Paper("y", 2025, "u2")])
    abroad = Candidate("A2", "Abroad", "HKU", papers=[Paper("z", 2026, "u3")], abroad=["MIT (US)"])
    assert [c.name for c in experts.rank([local, abroad], True)] == ["Abroad", "Local"]
    assert [c.name for c in experts.rank([local, abroad], False)] == ["Local", "Abroad"]


def test_about_line_names_the_latest_paper_and_career() -> None:
    person = Candidate(
        "A1",
        "Chan Tai Man",
        "PolyU",
        topics={"AI/ML in water"},
        papers=[Paper("Older", 2024, "u1"), Paper("Newest", 2026, "u2")],
        h_index=30,
        abroad=["University of Sheffield (GB)"],
    )
    assert experts.about(person, SINCE) == (
        '2 papers since 2023 (AI/ML in water), latest "Newest" (2026) · h-index 30 · '
        "career outside Hong Kong: University of Sheffield (GB)"
    )


def fake_openalex(works: dict[str, list[dict[str, Any]]]) -> httpx.Client:
    """Answers works searches from `works` (keyed by the search text) and author lookups."""

    def handle(request: httpx.Request) -> httpx.Response:
        query = request.url.params["filter"]
        if request.url.path == "/authors":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "https://openalex.org/A1",
                            "orcid": "https://orcid.org/0000-0001",
                            "summary_stats": {"h_index": 30},
                            "affiliations": [{"institution": SHEFFIELD, "years": [2014, 2015]}],
                        }
                    ]
                },
            )
        search = query.split(",")[0].removeprefix("title_and_abstract.search:")
        return httpx.Response(200, json={"results": works.get(search, [])})

    return httpx.Client(transport=httpx.MockTransport(handle))


def test_find_adds_careers_and_skips_people_already_known() -> None:
    client = fake_openalex({'"leak detection"': [LEAKS, TRANSIENTS]})
    found = experts.find({"Leaks": ["leak detection"]}, SINCE, client=client)
    assert [(c.name, c.orcid, c.h_index, c.abroad) for c in found] == [
        ("Chan Tai Man", "https://orcid.org/0000-0001", 30, ["University of Sheffield (GB)"])
    ]
    assert found[0].profile_url == "https://orcid.org/0000-0001"
    assert (
        experts.find({"Leaks": ["leak detection"]}, SINCE, exclude=frozenset({"A1"}), client=client)
        == []
    )


def test_a_single_topic_with_no_exact_match_is_searched_loosely() -> None:
    client = fake_openalex({"leaks in pipes": [LEAKS]})
    found = experts.find({"leaks in pipes": ["leaks in pipes"]}, SINCE, client=client)
    assert [c.name for c in found] == ["Chan Tai Man"]


def test_contact_numbers_and_statuses_are_read_flexibly() -> None:
    assert parse_ref("P12") == parse_ref("p12") == parse_ref("#12") == parse_ref(" 12 ") == 12
    assert parse_ref("Jane Smith") is None
    assert normalize_status("Not now") == "not_now"
    assert normalize_status("to-contact") == "to_contact"


def test_linkedin_link_is_a_people_search_for_the_owner_to_open() -> None:
    person = Person(ref=3, name="Chan Tai Man", organization="PolyU")
    assert desk.linkedin_search(person) == (
        "https://www.linkedin.com/search/results/people/?keywords=Chan+Tai+Man+PolyU"
    )
    assert desk.linkedin_search(Person(ref=4, name="Duu\u2010Jong Lee")).endswith("=Duu-Jong+Lee")


def test_people_to_meet_come_on_the_suggest_day_only(monkeypatch: pytest.MonkeyPatch) -> None:
    person = Person(ref=3, name="Chan Tai Man", organization="PolyU", about="2 papers since 2023")
    monkeypatch.setattr(desk, "suggestions", lambda now, only_new: [person])
    monkeypatch.setattr(desk.store, "follow_ups_due", lambda today: [])
    monday = datetime(2026, 10, 26, 8, 0, tzinfo=HKT)
    message = desk.section(monday)
    assert message is not None
    assert message.startswith("**People to meet this week**\n• **P3** Chan Tai Man — PolyU")
    assert "[LinkedIn search](https://www.linkedin.com/search/results/people/" in message
    assert desk.section(datetime(2026, 10, 27, 8, 0, tzinfo=HKT)) is None  # Tuesday
