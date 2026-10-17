from datetime import UTC, datetime, timedelta

from mentor.channels.telegram.failover import Cloud, Standby

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


def run(standby: Standby, checks: list[Cloud], mac_answering: bool = False) -> list[str]:
    """One decision a minute; returns them."""
    return [
        standby.decide(cloud, mac_answering, NOW + timedelta(minutes=i))
        for i, cloud in enumerate(checks)
    ]


def test_a_healthy_cloud_is_left_alone() -> None:
    assert set(run(Standby(), [Cloud.HEALTHY] * 30)) == {"wait"}


def test_takes_over_on_the_second_check_without_a_heartbeat() -> None:
    assert run(Standby(), [Cloud.SILENT] * 2) == ["wait", "take_over"]


def test_takes_over_quickly_when_the_server_is_stopped() -> None:
    assert run(Standby(), [Cloud.STOPPED] * 2) == ["wait", "take_over"]


def test_waits_longer_when_the_server_may_just_be_hard_to_reach() -> None:
    decisions = run(Standby(), [Cloud.UNREACHABLE] * 10)
    assert decisions[:9] == ["wait"] * 9 and decisions[9] == "take_over"


def test_one_good_check_resets_the_count() -> None:
    checks = [Cloud.SILENT, Cloud.HEALTHY, Cloud.SILENT, Cloud.HEALTHY, Cloud.SILENT]
    assert "take_over" not in run(Standby(), checks)


def test_never_judges_the_cloud_while_the_mac_is_offline() -> None:
    assert set(run(Standby(), [Cloud.UNKNOWN] * 60)) == {"wait"}
    assert set(run(Standby(), [Cloud.UNKNOWN] * 60, mac_answering=True)) == {"wait"}


def test_hands_back_once_the_cloud_is_reachable_again() -> None:
    standby = Standby()
    standby.took_over(NOW)
    checks = [Cloud.STOPPED, Cloud.UNREACHABLE, Cloud.SILENT, Cloud.SILENT]
    assert run(standby, checks, mac_answering=True) == ["wait", "wait", "wait", "hand_back"]


def test_hands_back_at_once_if_the_server_restarted_its_own_bot() -> None:
    standby = Standby()
    standby.took_over(NOW)
    assert run(standby, [Cloud.HEALTHY], mac_answering=True) == ["hand_back"]


def test_a_failed_hand_back_is_retried_half_an_hour_later() -> None:
    standby = Standby()
    standby.took_over(NOW)
    standby.handed_back(NOW + timedelta(minutes=2))
    standby.took_over(NOW + timedelta(minutes=6))  # the cloud bot didn't come back

    soon = NOW + timedelta(minutes=10)
    assert standby.decide(Cloud.SILENT, True, soon) == "wait"
    assert standby.decide(Cloud.SILENT, True, soon) == "wait"
    later = NOW + timedelta(minutes=40)
    assert standby.decide(Cloud.SILENT, True, later) == "hand_back"
