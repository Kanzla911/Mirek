"""Internet: suchen, Seiten lesen, Dateien herunterladen, APIs aufrufen."""

import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

from . import Result, as_bool, resolve, tool

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head", "template"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "section", "article", "pre", "table", "ul", "ol"}

    def __init__(self, base=None):
        super().__init__()
        self.parts, self.skip, self.links, self.base = [], 0, [], base

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")
        if tag in ("h1", "h2", "h3"):
            self.parts.append("## ")
        elif tag == "li":
            self.parts.append("- ")
        if tag == "a" and self.base:
            href = dict(attrs).get("href")
            if href and not href.startswith(("#", "javascript:", "mailto:")):
                self.links.append(urllib.parse.urljoin(self.base, href))

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)

    def text(self):
        text = re.sub(r"[ \t\r\f\v]+", " ", "".join(self.parts))
        return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def html_to_text(markup, base=None):
    parser = _TextExtractor(base)
    parser.feed(markup)
    return (parser.text(), parser.links) if base else parser.text()


def _open(url, data=None, headers=None, method=None, timeout=25):
    h = {"User-Agent": USER_AGENT, "Accept-Language": "de,en;q=0.8"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    return urllib.request.urlopen(req, timeout=timeout)


def _get(url, timeout=25):
    with _open(url, timeout=timeout) as r:
        charset = r.headers.get_content_charset() or "utf-8"
        return r.headers.get_content_type(), r.read(5_000_000).decode(charset, "replace"), r.geturl()


@tool(
    "fetch_url",
    "Lädt eine Webseite und gibt ihren lesbaren Text zurück (optional mit Linkliste, um Unterseiten zu finden).",
    {
        "url": {"type": "string", "description": "Die URL"},
        "links": {"type": "boolean", "description": "Auch die Links der Seite auflisten"},
    },
    ["url"],
)
def fetch_url(ctx, url, links=False):
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    ctype, body, final = _get(url)
    if "html" not in ctype:
        return f"[{final}]\n{body}"
    text, found = html_to_text(body, final)
    out = f"[{final}]\n{text}"
    if as_bool(links) and found:
        out = out[:9000] + "\n\nLinks:\n" + "\n".join(list(dict.fromkeys(found))[:80])
    return out


def _ddg(query):
    results = []
    data = urllib.parse.urlencode({"q": query, "kl": "wt-wt"}).encode()
    with _open("https://html.duckduckgo.com/html/", data=data,
               headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=25) as r:
        body = r.read().decode("utf-8", "replace")
    links = re.findall(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', body, re.S)
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', body, re.S)
    for i, (href, title) in enumerate(links):
        if "uddg=" in href:
            href = urllib.parse.unquote(href.split("uddg=")[1].split("&")[0])
        if "duckduckgo.com/y.js" in href:  # Werbung
            continue
        results.append((html.unescape(html_to_text(title)), href, html_to_text(snippets[i]) if i < len(snippets) else ""))
    return results


def _ddg_lite(query):
    data = urllib.parse.urlencode({"q": query, "kl": "wt-wt"}).encode()
    with _open("https://lite.duckduckgo.com/lite/", data=data,
               headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=25) as r:
        body = r.read().decode("utf-8", "replace")
    links = re.findall(r"<a[^>]+href=\"([^\"]+)\"[^>]*class=['\"]result-link['\"][^>]*>(.*?)</a>", body, re.S)
    links += re.findall(r"<a[^>]+class=['\"]result-link['\"][^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", body, re.S)
    snippets = re.findall(r"class=['\"]result-snippet['\"][^>]*>(.*?)</td>", body, re.S)
    results = []
    for i, (href, title) in enumerate(links):
        if "uddg=" in href:
            href = urllib.parse.unquote(href.split("uddg=")[1].split("&")[0])
        results.append((html.unescape(html_to_text(title)), href, html_to_text(snippets[i]) if i < len(snippets) else ""))
    return results


@tool(
    "web_search",
    "Sucht im Internet und liefert Titel, Links und Kurztexte. Lies danach interessante Treffer mit fetch_url.",
    {
        "query": {"type": "string", "description": "Suchanfrage"},
        "max_results": {"type": "integer", "description": "Anzahl Ergebnisse (Standard 8)"},
    },
    ["query"],
)
def web_search(ctx, query, max_results=8):
    results = []
    errors = []
    for engine in (_ddg, _ddg_lite):
        try:
            results = engine(query)
        except (urllib.error.URLError, OSError) as e:
            errors.append(str(e))
            continue
        if results:
            break
    if not results:
        return "Keine Ergebnisse (Suchmaschine evtl. blockiert" + (f": {errors[0]}" if errors else "") + \
            "). Tipp: fetch_url mit einer konkreten Seite verwenden."
    n = max(1, min(int(max_results or 8), 20))
    return "\n".join(f"{i}. {t}\n   {u}\n   {s}" for i, (t, u, s) in enumerate(results[:n], 1))


@tool(
    "download_file",
    "Lädt eine Datei aus dem Internet herunter und speichert sie.",
    {
        "url": {"type": "string", "description": "Download-URL"},
        "path": {"type": "string", "description": "Zielpfad oder Ordner (Standard: Downloads)"},
    },
    ["url"],
    dangerous=True,
)
def download_file(ctx, url, path=None):
    from pathlib import Path

    target = resolve(ctx, path) if path else Path.home() / "Downloads"
    with _open(url, timeout=60) as r:
        if target.is_dir() or not path:
            target.mkdir(parents=True, exist_ok=True)
            name = urllib.parse.unquote(urllib.parse.urlparse(r.geturl()).path.rsplit("/", 1)[-1]) or "download"
            cd = r.headers.get("Content-Disposition", "")
            m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', cd)
            if m:
                name = urllib.parse.unquote(m.group(1))
            target = target / name
        target.parent.mkdir(parents=True, exist_ok=True)
        size = 0
        with open(target, "wb") as f:
            while chunk := r.read(1 << 16):
                f.write(chunk)
                size += len(chunk)
    from .files import IMAGE_EXT, human_size

    msg = f"Heruntergeladen: {target} ({human_size(size)})"
    return Result(msg, [target]) if target.suffix.lower() in IMAGE_EXT and ctx.vision else msg


@tool(
    "http_request",
    "Ruft eine Web-API auf (GET/POST/PUT/DELETE …) und liefert Status und Antwort. Für JSON-APIs, Webhooks, "
    "Smart-Home usw.",
    {
        "url": {"type": "string", "description": "URL"},
        "method": {"type": "string", "description": "HTTP-Methode (Standard GET)"},
        "headers": {"type": "object", "description": "Zusätzliche Header"},
        "body": {"description": "Body: Text oder JSON-Objekt"},
    },
    ["url"],
    dangerous=lambda a: str(a.get("method", "GET")).upper() not in ("GET", "HEAD"),
)
def http_request(ctx, url, method="GET", headers=None, body=None):
    headers = dict(headers or {})
    data = None
    if body is not None:
        if isinstance(body, (dict, list)):
            data = json.dumps(body).encode()
            headers.setdefault("Content-Type", "application/json")
        else:
            data = str(body).encode()
    try:
        with _open(url, data=data, headers=headers, method=str(method or "GET").upper(), timeout=30) as r:
            status, text = r.status, r.read(2_000_000).decode(r.headers.get_content_charset() or "utf-8", "replace")
    except urllib.error.HTTPError as e:
        status, text = e.code, e.read(200_000).decode("utf-8", "replace")
    return f"HTTP {status}\n{text}"
