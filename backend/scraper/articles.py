"""Public-news discovery and extraction. Never attempts to bypass a paywall."""
from __future__ import annotations

import asyncio
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus, urlparse
from xml.etree import ElementTree

import httpx
from bs4 import BeautifulSoup

PUBLISHERS = {
    "wsj": ("WSJ", "wsj.com"),
    "bloomberg": ("Bloomberg", "bloomberg.com"),
    "nyt": ("The New York Times", "nytimes.com"),
    "economist": ("The Economist", "economist.com"),
    "forbes": ("Forbes", "forbes.com"),
}
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; LocalResearchScraper/1.0; +http://localhost)"}


def _text_from_page(html: str) -> tuple[str, str | None]:
    soup = BeautifulSoup(html, "html.parser")
    for node in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
        node.decompose()
    description_node = soup.select_one('meta[name="description"]')
    description = description_node.get("content", "").strip() if description_node else None
    container = soup.select_one("article") or soup.select_one("main") or soup.body
    paragraphs = container.select("p") if container else []
    text = "\n\n".join(p.get_text(" ", strip=True) for p in paragraphs if len(p.get_text(" ", strip=True)) > 40)
    return text[:30000], description


async def _extract(client: httpx.AsyncClient, url: str) -> tuple[str, str | None, str, str | None]:
    try:
        response = await client.get(url)
        response.raise_for_status()
        if "text/html" not in response.headers.get("content-type", ""):
            return "", None, response.url.__str__(), "Not an HTML article"
        text, description = _text_from_page(response.text)
        # A short shell/consent/paywall page is not presented as article content.
        if len(text) < 180:
            return "", description, response.url.__str__(), "No publicly accessible article text"
        return text, description, response.url.__str__(), None
    except httpx.HTTPError as exc:
        return "", None, url, f"Article unavailable: {exc.__class__.__name__}"


async def _search_publisher(client: httpx.AsyncClient, company: str, ceo: str, label: str, domain: str) -> tuple[list[dict], list[str]]:
    query = f'({company} OR "{ceo}") AI interview site:{domain}' if ceo else f'{company} AI interview site:{domain}'
    rss_url = "https://news.google.com/rss/search?q=" + quote_plus(query) + "&hl=en-US&gl=US&ceid=US:en"
    errors: list[str] = []
    try:
        response = await client.get(rss_url)
        response.raise_for_status()
        root = ElementTree.fromstring(response.content)
    except (httpx.HTTPError, ElementTree.ParseError) as exc:
        return [], [f"{label}: search unavailable ({exc.__class__.__name__})"]
    candidates = []
    for node in root.findall(".//item")[:5]:
        link = node.findtext("link")
        title = node.findtext("title")
        if not link or not title:
            continue
        date = node.findtext("pubDate")
        published = None
        if date:
            try:
                published = parsedate_to_datetime(date).isoformat()
            except (TypeError, ValueError):
                published = date
        candidates.append({"title": title, "url": link, "published_at": published})
    extracted = await asyncio.gather(*[_extract(client, item["url"]) for item in candidates])
    results = []
    for item, (content, description, final_url, error) in zip(candidates, extracted):
        if error:
            errors.append(f"{label}: {error}")
            continue
        # Google may resolve to a syndication page; retain only publisher pages.
        if domain not in urlparse(final_url).netloc:
            errors.append(f"{label}: skipped a result that did not resolve to {domain}")
            continue
        results.append({**item, "url": final_url, "publisher": label, "source_type": "article", "description": description, "content": content, "transcript": None})
    return results, errors


async def search_articles(company: str, ceo: str, sources: list[str]) -> tuple[list[dict], list[str]]:
    selected = [(key, *PUBLISHERS[key]) for key in sources if key in PUBLISHERS]
    if not selected:
        return [], []
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=18.0) as client:
        groups = await asyncio.gather(*[_search_publisher(client, company, ceo, label, domain) for _, label, domain in selected])
    results = [item for group, _ in groups for item in group]
    errors = [error for _, group_errors in groups for error in group_errors]
    return results, errors
