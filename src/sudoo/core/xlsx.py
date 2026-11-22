"""A minimal XLSX writer: one sheet of text cells, a bold, frozen header row, column widths. Enough for exports
(the audit log) without a spreadsheet library: an XLSX file is a zip of a few XML parts (ECMA-376, Office Open
XML). Cells are inline strings, so no shared-string table is needed; text is never read as a formula.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Callable
from xml.sax.saxutils import escape

_INVALID_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
MAX_CELL = 32767  # characters Excel keeps in a cell

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>"""

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>"""

_WORKBOOK_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""

_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>
<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""

_APP = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"><Application>Sudoo.sh</Application></Properties>"""


def column_name(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    name = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        name = chr(65 + rest) + name
    return name


def _text(value: object) -> str:
    return escape(_INVALID_XML.sub("", str(value))[:MAX_CELL])


def _sheet(rows: list[list[str]], widths: list[float] | None, write: Callable[[str], object]) -> None:
    """The worksheet's XML, written piece by piece (a large export never sits in memory as one text)."""
    out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
           '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
           '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" '
           'state="frozen"/></sheetView></sheetViews>']  # fmt: skip
    if widths:
        out.append("<cols>")
        out += [f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths)]
        out.append("</cols>")
    out.append("<sheetData>")
    for number, row in enumerate(rows, start=1):
        style = ' s="1"' if number == 1 else ""
        cells = "".join(
            f'<c r="{column_name(i)}{number}" t="inlineStr"{style}><is><t xml:space="preserve">{_text(v)}</t></is></c>'
            for i, v in enumerate(row)
        )
        out.append(f'<row r="{number}">{cells}</row>')
        if len(out) >= 500:
            write("".join(out))
            out.clear()
    out.append("</sheetData>")
    if rows:
        out.append(f'<autoFilter ref="A1:{column_name(max(len(rows[0]), 1) - 1)}{len(rows)}"/>')
    out.append("</worksheet>")
    write("".join(out))


def workbook(title: str, rows: list[list[str]], widths: list[float] | None = None) -> bytes:
    """An XLSX file with one sheet named `title` (at most 31 characters) holding `rows` (the first is the header)."""
    name = _text(re.sub(r"[\[\]:*?/\\]", " ", title)[:31] or "Sheet1")
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{name}" sheetId="1" r:id="rId1"/></sheets>'
        + (f'<definedNames><definedName name="_xlnm._FilterDatabase" localSheetId="0" hidden="1">'
           f"'{name}'!$A$1:${column_name(max(len(rows[0]), 1) - 1)}${len(rows)}</definedName></definedNames>"
           if rows else "")
        + "</workbook>"
    )  # fmt: skip
    core = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>' + name + "</dc:title><dc:creator>Sudoo.sh"
        "</dc:creator></cp:coreProperties>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("_rels/.rels", _ROOT_RELS)
        archive.writestr("docProps/core.xml", core)
        archive.writestr("docProps/app.xml", _APP)
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", _WORKBOOK_RELS)
        archive.writestr("xl/styles.xml", _STYLES)
        with archive.open("xl/worksheets/sheet1.xml", "w") as sheet:
            _sheet(rows, widths, lambda text: sheet.write(text.encode()))
    return buffer.getvalue()
