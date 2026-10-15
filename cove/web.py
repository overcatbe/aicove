# Web search for the chat: switched on per chat ("Web: on" above the input). The persona gets
# two tools, web_search and read_web_page. Searching goes to DuckDuckGo (nothing to install, no
# key) or to a SearXNG of your own (setting web_search). DuckDuckGo shows a challenge instead of
# results after many searches in a short time; then Wikipedia's search is used for the moment. Pages are read as text, at most a few
# MB; only http and https, and never an address on this machine or the local network, so a
# model can't reach llama-server, local web servers or the router through it.

import ipaddress
import json
import socket
import urllib.error
import urllib.parse
import urllib.request

from .knowledge import strip_tags
from .settings import S

AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"
MAX_BYTES = 3_000_000
PART = 10_000
HINT = ("You can search the web with web_search and read pages with read_web_page. Use them for "
        "anything current or that you are not sure of, and name the pages you used.")


def tool(name, description, **params):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object",
                       "properties": {k: {"type": t, "description": d} for k, (t, d, _) in params.items()},
                       "required": [k for k, (_, _, need) in params.items() if need]}}}


TOOLS = [
    tool("web_search", "Search the web. Returns titles, addresses and short snippets of the best results; "
         "read a page with read_web_page.",
         query=("string", "What to search for, as you would type it into a search engine.", True)),
    tool("read_web_page", "Read a web page as text. Long pages come in parts.",
         url=("string", "The page's address, starting with http:// or https://.", True),
         part=("integer", "Which part of a long page (1 = the beginning).", False)),
]
NAMES = {t["function"]["name"] for t in TOOLS}


class Redirects(urllib.request.HTTPRedirectHandler):
    """Follows a redirect only to an address that may be read."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        refusal = why_not(newurl)
        if refusal:
            raise urllib.error.URLError(f"the page sends on to {newurl}: {refusal}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


OPENER = urllib.request.build_opener(Redirects)


def get(url, data=None, timeout=30):
    """(final address, content type, bytes) of a page."""
    req = urllib.request.Request(url, data, {"User-Agent": AGENT, "Accept-Language": "en, de;q=0.8"})
    with OPENER.open(req, timeout=timeout) as r:
        return r.geturl(), r.headers.get_content_type(), r.read(MAX_BYTES)


def search(query):
    where = S.web_search.strip() or "duckduckgo"
    rows = []
    if "://" in where:
        _, _, body = get(where.rstrip("/") + "/search?" + urllib.parse.urlencode({"q": query, "format": "json"}))
        for r in json.loads(body).get("results", [])[:8]:
            rows.append((r.get("title", ""), r.get("url", ""), r.get("content", "")))
    else:
        _, _, body = get("https://html.duckduckgo.com/html/", urllib.parse.urlencode({"q": query}).encode())
        page = body.decode("utf-8", errors="replace")
        if 'class="result__a"' not in page and ("anomaly" in page or "challenge" in page):
            return wikipedia(query)
        for block in page.split('class="result__a"')[1:9]:
            href = block.partition('href="')[2].partition('"')[0]
            title = strip_tags(block.partition(">")[2].partition("</a>")[0])
            snippet = strip_tags(block.partition('class="result__snippet"')[2].partition(">")[2].partition("</a>")[0])
            # Some results go through DuckDuckGo's own redirect.
            if "uddg=" in href:
                href = urllib.parse.unquote(href.partition("uddg=")[2].partition("&")[0])
            if href.startswith("//"):
                href = "https:" + href
            rows.append((title, href, snippet))
    if not rows:
        return f"No results for \"{query}\"."
    return f"Results for \"{query}\":\n\n" + "\n\n".join(
        f"{n}. {t}\n{u}\n{s}" for n, (t, u, s) in enumerate(rows, 1))


def wikipedia(query):
    """Wikipedia's own search, for when DuckDuckGo doesn't answer."""
    found = []
    for lang in ("en", "de"):
        url = f"https://{lang}.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
            {"action": "query", "list": "search", "srsearch": query, "srlimit": 5, "format": "json"})
        _, _, body = get(url)
        for r in json.loads(body).get("query", {}).get("search", []):
            found.append((r["title"], f"https://{lang}.wikipedia.org/wiki/" + urllib.parse.quote(r["title"].replace(" ", "_")),
                          strip_tags(r.get("snippet", ""))))
    if not found:
        return (f"The web search is resting (too many searches in a short time) and Wikipedia has nothing "
                f"for \"{query}\". Try again in a few minutes.")
    return (f"The web search is resting (too many searches in a short time), so these are Wikipedia's "
            f"results for \"{query}\":\n\n" + "\n\n".join(f"{n}. {t}\n{u}\n{s}" for n, (t, u, s) in enumerate(found, 1)))


def why_not(url):
    """Why this address may not be read, or None."""
    parts = urllib.parse.urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return "Only http:// and https:// addresses can be read."
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(parts.hostname, None)}
    except OSError:
        return f"{parts.hostname} can't be found."
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%")[0])
        if not ip.is_global:
            return "Addresses on this computer or the local network can't be read."
    return None


def read_page(url, part=1):
    refusal = why_not(url)
    if refusal:
        return refusal
    final, kind, body = get(url)
    # A redirect could lead somewhere local; check the address it ended at too.
    refusal = why_not(final)
    if refusal:
        return refusal
    if kind not in ("text/html", "application/xhtml+xml", "text/plain", "application/json", "text/xml"):
        return f"{final} is not a page with text ({kind})."
    text = body.decode("utf-8", errors="replace")
    if kind != "text/plain" and kind != "application/json":
        text = strip_tags(text)
    parts = max(1, -(-len(text) // PART))
    part = min(max(int(part or 1), 1), parts)
    more = f" Part {part} of {parts}; read_web_page with part={part + 1} for more." if part < parts else \
        (f" Part {part} of {parts}." if parts > 1 else "")
    return f"{final}.{more}\n\n" + text[(part - 1) * PART:part * PART]


def run(name, arguments):
    try:
        a = json.loads(arguments or "{}") if isinstance(arguments, str) else dict(arguments or {})
    except ValueError:
        return "The arguments were not valid JSON. Call the tool again with proper arguments."
    try:
        if name == "web_search":
            return search(str(a["query"]))
        return read_page(str(a["url"]), a.get("part", 1))
    except KeyError as e:
        return f"Missing argument {e} for {name}."
    except (OSError, ValueError, TypeError) as e:
        return f"That didn't work: {e}"


def describe(name, arguments):
    try:
        a = json.loads(arguments or "{}")
    except ValueError:
        a = {}
    if name == "web_search":
        return f"Searched the web for \"{a.get('query', '')}\""
    return f"Read {a.get('url', '')}" + (f" (part {a['part']})" if a.get("part", 1) not in (1, None) else "")
