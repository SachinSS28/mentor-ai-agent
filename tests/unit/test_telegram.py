from mentor.channels.telegram.bot import decide, pairing_reply, split_message


def test_answers_only_the_allowed_chat() -> None:
    assert decide(42, allowed_chat_id=42) == "answer"
    assert decide(99, allowed_chat_id=42) == "ignore"


def test_pairs_before_an_allowed_chat_is_set() -> None:
    assert decide(42, allowed_chat_id=None) == "pair"
    assert "MENTOR_TELEGRAM_ALLOWED_CHAT_ID=42" in pairing_reply(42)


def test_long_replies_are_split_under_telegrams_limit() -> None:
    parts = split_message("x" * 9000)
    assert [len(p) for p in parts] == [4096, 4096, 808]
    assert split_message("") == [""]
