"""Builds Mentor's system prompt from config/persona.md and the charter."""

from datetime import datetime

from mentor.charter import Charter

LANGUAGE_RULES = {
    "english": (
        "Reply in English by default; switch to Hinglish or Hindi when {owner} writes that way."
    ),
    "hinglish": "Reply in Hinglish (mixed Hindi and English) by default.",
    "hindi": "Reply in Hindi by default.",
}

TONE_RULES = {
    "gentle": "Be encouraging; raise problems softly.",
    "direct": (
        "Be warm but honest: point out overcommitment, slipping deadlines and low-value "
        "meetings, and suggest what to drop."
    ),
    "tough_love": "Be blunt about slips and excuses; hold {owner} to every commitment.",
}


def build_system_prompt(charter: Charter, persona: str, now: datetime) -> str:
    owner = charter.owner.name
    body = persona.format(
        assistant_name=charter.assistant.name,
        owner_name=owner,
        language_rule=LANGUAGE_RULES[charter.assistant.language].format(owner=owner),
        tone_rule=TONE_RULES[charter.assistant.coaching_tone].format(owner=owner),
    )
    context = (
        f"Now: {now:%A %d %B %Y, %H:%M} ({charter.owner.timezone}). "
        f"{owner} is based in {charter.owner.city}."
    )
    return f"{body.strip()}\n\n{context}"
