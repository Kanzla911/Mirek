"""Text aus Dokumenten holen: PDF, Word, Excel, PowerPoint, OpenDocument – ohne Zusatzpakete."""

import re
import shutil
import subprocess
import zipfile
import zlib
from xml.etree import ElementTree

DOC_EXT = {".pdf", ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp", ".epub"}


def extract_text(path):
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return pdf_text(path)
    if suffix == ".docx":
        return _xml_text(path, ["word/document.xml"], para="p")
    if suffix == ".pptx":
        with zipfile.ZipFile(path) as z:
            slides = sorted(
                (n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                key=lambda n: int(re.findall(r"\d+", n)[-1]),
            )
        return _xml_text(path, slides, para="p", headings="--- Folie {} ---")
    if suffix == ".xlsx":
        return xlsx_text(path)
    if suffix in (".odt", ".ods", ".odp"):
        return _xml_text(path, ["content.xml"], para=("p", "h", "table-row"))
    if suffix == ".epub":
        with zipfile.ZipFile(path) as z:
            pages = [n for n in z.namelist() if n.endswith((".xhtml", ".html", ".htm"))]
        from .web import html_to_text

        with zipfile.ZipFile(path) as z:
            return "\n\n".join(html_to_text(z.read(n).decode("utf-8", "replace")) for n in pages)
    raise ValueError(f"Unbekanntes Dokumentformat: {suffix}")


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _xml_text(path, members, para="p", headings=None):
    paras = para if isinstance(para, tuple) else (para,)
    out = []
    with zipfile.ZipFile(path) as z:
        for i, member in enumerate(members, 1):
            if headings:
                out.append(headings.format(i))
            root = ElementTree.fromstring(z.read(member))
            for el in root.iter():
                if _local(el.tag) in paras:
                    text = "".join(t for t in el.itertext()).strip()
                    if text:
                        out.append(text)
    return "\n".join(out)


def xlsx_text(path):
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            for si in ElementTree.fromstring(z.read("xl/sharedStrings.xml")):
                shared.append("".join(si.itertext()))
        sheet_names = []
        if "xl/workbook.xml" in names:
            for el in ElementTree.fromstring(z.read("xl/workbook.xml")).iter():
                if _local(el.tag) == "sheet":
                    sheet_names.append(el.get("name"))
        sheets = sorted((n for n in names if re.match(r"xl/worksheets/sheet\d+\.xml$", n)),
                        key=lambda n: int(re.findall(r"\d+", n)[-1]))
        out = []
        for i, member in enumerate(sheets):
            out.append(f"--- Tabelle: {sheet_names[i] if i < len(sheet_names) else i + 1} ---")
            for row in ElementTree.fromstring(z.read(member)).iter():
                if _local(row.tag) != "row":
                    continue
                cells = []
                for c in row:
                    if _local(c.tag) != "c":
                        continue
                    value = ""
                    for child in c:
                        if _local(child.tag) == "v":
                            value = child.text or ""
                        elif _local(child.tag) == "is":
                            value = "".join(child.itertext())
                    if c.get("t") == "s" and value.isdigit() and int(value) < len(shared):
                        value = shared[int(value)]
                    cells.append(f"{c.get('r', '')}={value}")
                if cells:
                    out.append(" | ".join(cells))
        return "\n".join(out)


def pdf_text(path):
    try:  # beste Qualität, falls installiert
        import pypdf

        reader = pypdf.PdfReader(str(path))
        return "\n\n".join(f"--- Seite {i} ---\n{p.extract_text() or ''}" for i, p in enumerate(reader.pages, 1))
    except ImportError:
        pass
    if shutil.which("pdftotext"):
        p = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, errors="replace")
        if p.returncode == 0 and p.stdout.strip():
            return p.stdout
    text = _pdf_fallback(path.read_bytes())
    if not text.strip():
        return ("(Kein Text gefunden – evtl. eingescanntes PDF. Für bessere Ergebnisse: "
                "pip install pypdf, oder als Bild an ein Vision-Modell geben.)")
    return text


def _pdf_string(raw):
    raw = re.sub(rb"\\([nrtbf()\\])", lambda m: {b"n": b"\n", b"r": b"", b"t": b"\t", b"b": b"", b"f": b""}.get(m.group(1), m.group(1)), raw)
    raw = re.sub(rb"\\(\d{1,3})", lambda m: bytes([int(m.group(1), 8) & 255]), raw)
    return raw.decode("latin-1")


def _pdf_fallback(data):
    """Sehr einfacher PDF-Textleser für unkomprimierte und Flate-komprimierte Seiten."""
    out = []
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, re.S):
        chunk = m.group(1)
        try:
            chunk = zlib.decompress(chunk)
        except zlib.error:
            pass
        if b"BT" not in chunk:
            continue
        for block in re.findall(rb"BT(.*?)ET", chunk, re.S):
            line = []
            for tok in re.finditer(rb"\((.*?)(?<!\\)\)\s*Tj|\[(.*?)\]\s*TJ|(T\*|Td|TD|')", block, re.S):
                if tok.group(1) is not None:
                    line.append(_pdf_string(tok.group(1)))
                elif tok.group(2) is not None:
                    line.append("".join(_pdf_string(s) for s in re.findall(rb"\((.*?)(?<!\\)\)", tok.group(2), re.S)))
                elif line:
                    out.append("".join(line))
                    line = []
            if line:
                out.append("".join(line))
    return "\n".join(out)
