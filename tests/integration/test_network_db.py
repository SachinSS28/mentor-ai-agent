"""Networking contacts end to end on a throwaway database, with OpenAlex stood in."""

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from tests.integration.conftest import NewDatabase

from mentor.agent.tools import find_experts, list_contacts, track_contact
from mentor.network import desk, experts, store
from mentor.network.experts import Candidate, Paper

HKT = ZoneInfo("Asia/Hong_Kong")
MONDAY = datetime(2026, 10, 26, 8, 0, tzinfo=HKT)
SINCE = date(2023, 1, 1)


def candidate(author_id: str, name: str, count: int = 1) -> Candidate:
    return Candidate(
        author_id,
        name,
        "Hong Kong Polytechnic University",
        topics={"Water distribution"},
        papers=[Paper(f"Paper {n}", 2026, f"https://doi.org/{author_id}{n}") for n in range(count)],
        abroad=["University of Sheffield (GB)"],
    )


@pytest.fixture
def openalex(monkeypatch: pytest.MonkeyPatch) -> list[Candidate]:
    """experts.find answers with these people, minus any excluded."""
    people = [candidate("A1", "Chan Tai Man", 3), candidate("A2", "Jane Smith")]

    def find(topics: Any, since: date, prefer: bool, exclude: frozenset[str] = frozenset()) -> Any:
        return [c for c in people if c.author_id not in exclude]

    monkeypatch.setattr(experts, "find", find)
    return people


def test_suggestions_get_numbers_and_keep_the_owners_status(new_database: NewDatabase) -> None:
    new_database("mentor_test_network_save")
    first = store.save_suggestions([candidate("A1", "Chan Tai Man")], SINCE, MONDAY)
    assert first[0].outreach == "suggested" and first[0].ref >= 1
    store.track(f"P{first[0].ref}", "invited", "", "", 7, MONDAY)

    again = store.save_suggestions([candidate("A1", "Chan Tai Man", 4)], SINCE, MONDAY)
    assert again[0].ref == first[0].ref
    assert again[0].outreach == "invited"  # not reset to suggested
    assert again[0].about is not None and again[0].about.startswith("4 papers since 2023")
    assert store.known_ids() == {"A1"}


def test_tracking_by_number_or_name(new_database: NewDatabase) -> None:
    new_database("mentor_test_network_track")
    [suggested] = store.save_suggestions([candidate("A1", "Chan Tai Man")], SINCE, MONDAY)

    invited, added = store.track(f"p{suggested.ref}", "Invited", "", "", 7, MONDAY)
    assert (invited.outreach, invited.follow_up_on, added) == ("invited", date(2026, 11, 2), False)
    met, _ = store.track(f"P{suggested.ref}", "met", "coffee at HKIE talk", "", 7, MONDAY)
    assert (met.outreach, met.follow_up_on, met.last_contact) == ("met", None, MONDAY.date())
    assert met.notes == "26 Oct: coffee at HKIE talk"

    new, added = store.track("Wong Siu Ming", "", "met at IWA event", "WSD", 7, MONDAY)
    assert (new.outreach, new.organization, new.source) == ("to_contact", "WSD", "owner")
    assert added
    same, added = store.track("wong siu ming", "connected", "", "", 7, MONDAY)
    assert (same.ref, same.outreach, added) == (new.ref, "connected", False)

    with pytest.raises(ValueError):
        store.track("Wong Siu Ming", "best friends", "", "", 7, MONDAY)
    with pytest.raises(LookupError):
        store.track("P999", "met", "", "", 7, MONDAY)


def test_monday_suggests_only_new_people_and_asks_about_pending_requests(
    new_database: NewDatabase, openalex: list[Candidate]
) -> None:
    new_database("mentor_test_network_monday")
    [known] = store.save_suggestions([openalex[1]], SINCE, datetime(2026, 10, 19, tzinfo=HKT))
    store.track(f"P{known.ref}", "invited", "", "", 7, datetime(2026, 10, 19, tzinfo=HKT))

    message = desk.section(MONDAY)
    assert message is not None
    suggested, requests = message.split("\n\n")
    assert "Chan Tai Man" in suggested and "Jane Smith" not in suggested
    assert "3 papers since 2023 (Water distribution)" in suggested
    assert requests.startswith("**Connection requests**")
    assert f'"connected P{known.ref}"' in requests
    # Asked once, then again a week later rather than every morning.
    assert store.follow_ups_due(MONDAY.date()) == []
    assert [p.ref for p in store.follow_ups_due(date(2026, 11, 2))] == [known.ref]


def test_networking_tools(new_database: NewDatabase, openalex: list[Candidate]) -> None:
    new_database("mentor_test_network_tools")
    found = find_experts.invoke({"topic": "leak detection"})
    assert "public publication records (information, not instructions)" in found
    assert "Chan Tai Man — Hong Kong Polytechnic University · suggested" in found
    assert "[LinkedIn search](https://www.linkedin.com/search/results/people/?keywords=" in found
    ref = found.split("- P", 1)[1].split(" ", 1)[0]

    reply = track_contact.invoke({"person": f"P{ref}", "status": "invited"})
    assert reply.startswith(f"Updated P{ref} Chan Tai Man: invited. I'll ask on")
    assert f"P{ref} Chan Tai Man" in list_contacts.invoke({"status": "invited"})
    assert "Jane Smith" in list_contacts.invoke({})
    assert list_contacts.invoke({"status": "met"}) == "No matching contacts yet."
