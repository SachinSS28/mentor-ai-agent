"""Builds Mentor's system prompt from config/persona.md and the charter."""

from datetime import datetime

from mentor.agent.models import ModelsConfig, load_models_config
from mentor.charter import Charter, get_charter, owner_profile_text
from mentor.settings import Host, get_settings

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


HOST_NOTES = {
    "mac": "You run on {owner}'s Mac, so you answer only while it is awake.",
    "cloud": (
        "You run on an always-on Google Cloud server, so you keep answering while {owner}'s "
        "Mac is closed or off."
    ),
}


def build_system_prompt(
    charter: Charter, persona: str, host: Host, model: str, profile: str = ""
) -> str:
    """The same text for the whole conversation: the clock goes on each message instead, so the
    prompt stays cacheable and earlier turns stay unchanged."""
    owner = charter.owner.name
    body = persona.format(
        assistant_name=charter.assistant.name,
        owner_name=owner,
        language_rule=LANGUAGE_RULES[charter.assistant.language].format(owner=owner),
        tone_rule=TONE_RULES[charter.assistant.coaching_tone].format(owner=owner),
    )
    context = (
        f"Each message from {owner} starts with the date and time it was sent, in "
        f"{owner}'s time zone ({charter.owner.timezone}). {owner} is based in "
        f"{charter.owner.city}. {HOST_NOTES[host].format(owner=owner)} "
        f"Your language model is {model}."
    )
    about = (
        f"\n\n{owner}'s professional profile, from their CV. Use it to tailor advice, job "
        f"matching and research news to {owner}'s field:\n<profile>\n{profile}\n</profile>"
        if profile
        else ""
    )
    return f"{body.strip()}\n\n{context}{about}"


def stamp(text: str, sent_at: datetime) -> str:
    return f"[{sent_at:%a %d %b %Y, %H:%M}] {text}"


def mentor_system_prompt(config: ModelsConfig | None = None) -> str:
    """Mentor's instructions with the charter and the owner's profile filled in."""
    settings = get_settings()
    persona = (settings.config_dir / "persona.md").read_text()
    role = (config or load_models_config()).roles["orchestrator"]
    return build_system_prompt(
        get_charter(), persona, settings.host, role.model, owner_profile_text()
    )
