from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from mentor.practice import dhamma
from mentor.practice.moon import uposatha_on

HKT = ZoneInfo("Asia/Hong_Kong")


@pytest.mark.parametrize(
    ("day", "phase"),
    [  # reference dates from PyEphem, in Hong Kong time
        (date(2026, 10, 26), "full moon"),
        (date(2026, 11, 9), "new moon"),
        (date(2026, 11, 24), "full moon"),
        (date(2026, 12, 9), "new moon"),
        (date(2027, 1, 8), "new moon"),
        (date(2026, 10, 25), None),
        (date(2026, 11, 1), None),
    ],
)
def test_uposatha_days_follow_the_moon_in_local_time(day: date, phase: str | None) -> None:
    assert uposatha_on(day, HKT) == phase


def test_every_teaching_names_its_source_and_a_practice() -> None:
    config = dhamma.load()
    assert len(config.teachings) >= 30 and len(config.evening_prompts) >= 7
    for teaching in config.teachings:
        assert teaching.source and teaching.teaching and teaching.practice, teaching.theme


def test_one_teaching_per_day_cycling_through_the_list() -> None:
    teachings = dhamma.load().teachings
    days = [date(2026, 10, 20) + timedelta(days=n) for n in range(len(teachings))]
    assert dhamma.pick(teachings, days[0]) == dhamma.pick(teachings, days[0])
    assert len({dhamma.pick(teachings, d).theme for d in days}) == len(teachings)


def test_morning_teaching_is_marked_as_a_paraphrase_with_its_source() -> None:
    text = dhamma.morning_section(datetime(2026, 10, 20, 8, 0, tzinfo=HKT))
    assert text is not None and text.startswith("**Dhamma for today: ")
    assert ", paraphrased)" in text and "\nPractice: " in text
    assert "Uposatha" not in text  # 20 Oct is not a moon day


def test_uposatha_is_mentioned_the_evening_before_and_on_the_day() -> None:
    full_moon = datetime(2026, 10, 26, 8, 0, tzinfo=HKT)
    on_the_day = dhamma.morning_section(full_moon)
    assert on_the_day is not None and "Today is Uposatha (full moon)" in on_the_day
    evening_before = dhamma.evening_message(full_moon - timedelta(hours=11))  # 25 Oct, 21:00
    assert "Tomorrow is Uposatha (full moon)" in evening_before
    assert "**Evening reflection**" in evening_before


def test_evening_reflection_goes_out_once_after_its_time(monkeypatch: pytest.MonkeyPatch) -> None:
    saved: dict[str, str] = {}
    monkeypatch.setattr(dhamma, "get_state", saved.get)
    monkeypatch.setattr(dhamma, "set_state", saved.__setitem__)
    evening = datetime(2026, 10, 20, 21, 30, tzinfo=HKT)
    assert dhamma.evening_tick(evening - timedelta(hours=1)) is None
    assert dhamma.evening_tick(evening) is not None
    assert dhamma.evening_tick(evening + timedelta(minutes=15)) is None
    assert dhamma.evening_tick(evening + timedelta(days=1)) is not None
