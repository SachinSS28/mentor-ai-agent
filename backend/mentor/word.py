"""Word documents Mentor writes for the owner (cover letters, letters, reports, CV points), saved
in Documents/Mentor on the Mac.

A .docx file is a zip of a few XML parts. This writes a small, valid one from Markdown (headings,
paragraphs with line breaks, bullet and numbered lists, bold, italic, code and tables) with the
standard library only, so it works offline and needs nothing installed.
"""

import io
import re
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, unquote
from xml.sax.saxutils import escape

from mentor.settings import get_settings

# A link to a saved document in a reply: the chat page serves it, Telegram sends the file.
FILE_LINK = re.compile(r"\[([^\]\n]+)\]\(/files/([^)\s]+)\)")
HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*#*$")
BULLET = re.compile(r"^\s*[-*•]\s+(.*)$")
NUMBERED = re.compile(r"^\s*\d+[.)]\s+(.*)$")
RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
INLINE = re.compile(
    r"\*\*(.+?)\*\*"  # bold
    r"|(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])"  # italic
    r"|`([^`]+)`"  # code
    r"|\[([^\]]+)\]\(([^)\s]+)\)"  # link
)
NOT_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")
UNSAFE_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
DOC = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
BULLETS_ID = 1  # numbering id shared by every bullet list; numbered lists get their own


@dataclass
class Body:
    parts: list[str] = field(default_factory=list)
    numbered: list[int] = field(default_factory=list)  # numbering ids, one per numbered list


def text_run(text: str, bold: bool = False, italic: bool = False, code: bool = False) -> str:
    props = ("<w:b/>" if bold else "") + ("<w:i/>" if italic else "")
    if code:
        props += '<w:rFonts w:ascii="Consolas" w:hAnsi="Consolas"/>'
    lines = escape(NOT_XML.sub("", text)).split("\n")
    body = "<w:br/>".join(f'<w:t xml:space="preserve">{line}</w:t>' for line in lines)
    return f"<w:r>{f'<w:rPr>{props}</w:rPr>' if props else ''}{body}</w:r>"


def runs(text: str, bold: bool = False) -> str:
    """Inline Markdown as Word runs: **bold**, *italic*, `code`, [text](link) as "text (link)"."""
    out, last = [], 0
    for match in INLINE.finditer(text):
        out.append(text_run(text[last : match.start()], bold=bold))
        strong, emphasis, code, label, url = match.groups()
        if strong is not None:
            out.append(text_run(strong, bold=True))
        elif emphasis is not None:
            out.append(text_run(emphasis, bold=bold, italic=True))
        elif code is not None:
            out.append(text_run(code, bold=bold, code=True))
        else:
            shown = label if url.startswith("/") or url in label else f"{label} ({url})"
            out.append(text_run(shown, bold=bold))
        last = match.end()
    out.append(text_run(text[last:], bold=bold))
    return "".join(run for run in out if '<w:t xml:space="preserve"></w:t>' not in run)


def paragraph(content: str, style: str | None = None, number_id: int | None = None) -> str:
    props = f'<w:pStyle w:val="{style}"/>' if style else ""
    if number_id is not None:
        props += f'<w:numPr><w:ilvl w:val="0"/><w:numId w:val="{number_id}"/></w:numPr>'
    return f"<w:p>{f'<w:pPr>{props}</w:pPr>' if props else ''}{content}</w:p>"


def cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def table(rows: list[list[str]]) -> str:
    width = max(len(row) for row in rows)
    grid = "".join('<w:gridCol w:w="2000"/>' for _ in range(width))
    xml_rows = []
    for n, row in enumerate(rows):
        row = row + [""] * (width - len(row))
        width_props = '<w:tcPr><w:tcW w:w="0" w:type="auto"/></w:tcPr>'
        xml_cells = "".join(f"<w:tc>{width_props}{paragraph(runs(c, n == 0))}</w:tc>" for c in row)
        xml_rows.append(f"<w:tr>{xml_cells}</w:tr>")
    props = '<w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:w="0" w:type="auto"/></w:tblPr>'
    # An empty paragraph after a table keeps Word from joining it to what follows.
    return f"<w:tbl>{props}<w:tblGrid>{grid}</w:tblGrid>{''.join(xml_rows)}</w:tbl><w:p/>"


def body(markdown: str) -> Body:
    """Markdown blocks as Word paragraphs and tables. A blank line starts a new paragraph; a
    single line break stays a line break (as in a letter's address)."""
    built = Body()
    lines = markdown.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if not line.strip() or RULE.match(line):
            i += 1
        elif heading := HEADING.match(line.strip()):
            level = len(heading.group(1))
            built.parts.append(paragraph(runs(heading.group(2)), f"Heading{level}"))
            i += 1
        elif line.lstrip().startswith("|"):
            rows = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                if not TABLE_SEPARATOR.match(lines[i]):
                    rows.append(cells(lines[i]))
                i += 1
            built.parts.append(table(rows))
        elif BULLET.match(line):
            while i < len(lines) and (item := BULLET.match(lines[i])):
                built.parts.append(paragraph(runs(item.group(1)), "ListParagraph", BULLETS_ID))
                i += 1
        elif NUMBERED.match(line):
            number_id = BULLETS_ID + 1 + len(built.numbered)
            built.numbered.append(number_id)
            while i < len(lines) and (item := NUMBERED.match(lines[i])):
                built.parts.append(paragraph(runs(item.group(1)), "ListParagraph", number_id))
                i += 1
        else:
            text = []
            while i < len(lines) and lines[i].strip() and not starts_block(lines[i]):
                text.append(lines[i].strip())
                i += 1
            built.parts.append(paragraph(runs("\n".join(text))))
    return built


def starts_block(line: str) -> bool:
    stripped = line.strip()
    return bool(
        HEADING.match(stripped)
        or stripped.startswith("|")
        or BULLET.match(line)
        or NUMBERED.match(line)
        or RULE.match(line)
    )


STYLES = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="{W}">
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Calibri" w:cs="Calibri"/><w:sz w:val="22"/><w:szCs w:val="22"/><w:lang w:val="en-GB"/></w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:spacing w:after="160" w:line="276" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="240" w:after="120"/><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:color w:val="2F5D50"/><w:sz w:val="32"/><w:szCs w:val="32"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="200" w:after="100"/><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/><w:color w:val="2F5D50"/><w:sz w:val="26"/><w:szCs w:val="26"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="160" w:after="80"/><w:outlineLvl w:val="2"/></w:pPr><w:rPr><w:b/><w:sz w:val="24"/><w:szCs w:val="24"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/><w:qFormat/><w:pPr><w:spacing w:after="60"/><w:ind w:left="720"/></w:pPr></w:style>
<w:style w:type="table" w:styleId="TableGrid"><w:name w:val="Table Grid"/><w:tblPr><w:tblBorders><w:top w:val="single" w:sz="4" w:space="0" w:color="A6A6A6"/><w:left w:val="single" w:sz="4" w:space="0" w:color="A6A6A6"/><w:bottom w:val="single" w:sz="4" w:space="0" w:color="A6A6A6"/><w:right w:val="single" w:sz="4" w:space="0" w:color="A6A6A6"/><w:insideH w:val="single" w:sz="4" w:space="0" w:color="A6A6A6"/><w:insideV w:val="single" w:sz="4" w:space="0" w:color="A6A6A6"/></w:tblBorders><w:tblCellMar><w:left w:w="108" w:type="dxa"/><w:right w:w="108" w:type="dxa"/></w:tblCellMar></w:tblPr></w:style>
</w:styles>"""  # noqa: E501 (one style per line reads best)


def numbering(numbered: list[int]) -> str:
    level = '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="{fmt}"/><w:lvlText w:val="{text}"/><w:lvlJc w:val="left"/><w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr></w:lvl>'  # noqa: E501
    restart = '<w:lvlOverride w:ilvl="0"><w:startOverride w:val="1"/></w:lvlOverride>'
    lists = "".join(
        f'<w:num w:numId="{n}"><w:abstractNumId w:val="1"/>{restart}</w:num>' for n in numbered
    )
    return (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:numbering xmlns:w="{W}">'
        f'<w:abstractNum w:abstractNumId="0"><w:multiLevelType w:val="singleLevel"/>'
        f"{level.format(fmt='bullet', text='•')}</w:abstractNum>"
        f'<w:abstractNum w:abstractNumId="1"><w:multiLevelType w:val="singleLevel"/>'
        f"{level.format(fmt='decimal', text='%1.')}</w:abstractNum>"
        f'<w:num w:numId="{BULLETS_ID}"><w:abstractNumId w:val="0"/></w:num>{lists}</w:numbering>'
    )


def docx(title: str, markdown: str, author: str = "Mentor") -> bytes:
    """A Word document from Markdown. The title goes into the document's properties."""
    built = body(markdown)
    page = (
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1440" '
        'w:bottom="1440" w:left="1440" w:header="708" w:footer="708" w:gutter="0"/></w:sectPr>'
    )
    document = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:document xmlns:w="{W}">'
        f"<w:body>{''.join(built.parts) or '<w:p/>'}{page}</w:body></w:document>"
    )
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    parts = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'  # noqa: E501
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'  # noqa: E501
            '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'  # noqa: E501
            '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>'  # noqa: E501
            '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'  # noqa: E501
            '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'  # noqa: E501
            "</Types>"
        ),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<Relationships xmlns="{REL}">'
            f'<Relationship Id="rId1" Type="{DOC}/officeDocument" Target="word/document.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/'
            'relationships/metadata/core-properties" Target="docProps/core.xml"/>'
            f'<Relationship Id="rId3" Type="{DOC}/extended-properties" Target="docProps/app.xml"/>'
            "</Relationships>"
        ),
        "word/_rels/document.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<Relationships xmlns="{REL}">'
            f'<Relationship Id="rId1" Type="{DOC}/styles" Target="styles.xml"/>'
            f'<Relationship Id="rId2" Type="{DOC}/numbering" Target="numbering.xml"/>'
            "</Relationships>"
        ),
        "docProps/core.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<cp:coreProperties '
            'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f"<dc:title>{escape(title)}</dc:title><dc:creator>{escape(author)}</dc:creator>"
            f'<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>'
            f'<dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified>'
            "</cp:coreProperties>"
        ),
        "docProps/app.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Properties xmlns='
            '"http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
            "<Application>Mentor</Application></Properties>"
        ),
        "word/document.xml": document,
        "word/styles.xml": STYLES,
        "word/numbering.xml": numbering(built.numbered),
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, xml in parts.items():
            archive.writestr(name, xml)
    return buffer.getvalue()


def file_name(title: str, today: datetime) -> str:
    words = " ".join(UNSAFE_NAME.sub(" ", title).split())[:80].strip(" .") or "Mentor document"
    return f"{words} ({today:%Y-%m-%d}).docx"


def save(title: str, markdown: str, author: str = "Mentor") -> Path:
    """Saves the document in the documents folder, never over an existing file."""
    folder = get_settings().documents_dir
    folder.mkdir(parents=True, exist_ok=True)
    name = file_name(title, datetime.now())
    path, n = folder / name, 2
    while path.exists():
        path = folder / name.replace(".docx", f" {n}.docx")
        n += 1
    path.write_bytes(docx(title, markdown, author))
    return path


def link(path: Path) -> str:
    """The Markdown link to a saved document, as the chat page serves it."""
    return f"[{path.name}](/files/{quote(path.name)})"


def linked_files(text: str) -> list[str]:
    """Names of the saved documents a reply links to."""
    return [unquote(name) for _, name in FILE_LINK.findall(text)]


def find(name: str) -> Path | None:
    """A saved document by its file name; None for anything outside the documents folder."""
    folder = get_settings().documents_dir.resolve()
    if not name or Path(name).name != name or not name.endswith(".docx"):
        return None
    path = (folder / name).resolve()
    return path if path.parent == folder and path.is_file() else None
