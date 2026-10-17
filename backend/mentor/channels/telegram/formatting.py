"""Turns the model's Markdown into the HTML subset Telegram shows.

Telegram shows bold, italic, code and links but no headings or lists, and refuses a message
whose tags don't parse, so the bot sends plain text if this ever produces something it rejects.
"""

import html
import re

CODE = re.compile(r"```[\w+-]*\n?(.*?)```|`([^`\n]+)`", re.DOTALL)
HEADING = re.compile(r"^#{1,6}\s+(.+?)[\s#]*$", re.MULTILINE)
BULLET = re.compile(r"^(\s*)[*-]\s+", re.MULTILINE)
BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
ITALIC = re.compile(r"(?<![*\w])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![*\w])")
LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s\"]+)\)")
STASHED = re.compile(r"\x00(\d+)\x00")


def to_telegram_html(markdown: str) -> str:
    # Code is set aside first so nothing inside it is read as formatting.
    code: list[str] = []

    def stash(match: re.Match[str]) -> str:
        block, inline = match.groups()
        if block is not None:
            code.append(f"<pre>{html.escape(block.strip(), quote=False)}</pre>")
        else:
            code.append(f"<code>{html.escape(inline, quote=False)}</code>")
        return f"\x00{len(code) - 1}\x00"

    text = html.escape(CODE.sub(stash, markdown), quote=False)
    text = HEADING.sub(lambda m: f"<b>{m.group(1).replace('**', '')}</b>", text)
    text = BULLET.sub(r"\1• ", text)
    text = BOLD.sub(r"<b>\1</b>", text)
    text = ITALIC.sub(r"<i>\1</i>", text)
    text = LINK.sub(r'<a href="\2">\1</a>', text)
    return STASHED.sub(lambda m: code[int(m.group(1))], text)
