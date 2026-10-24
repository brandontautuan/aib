"""YouTube public search and optional captions retrieval, without an API key."""
from __future__ import annotations

import json
import re
from datetime import datetime
from urllib.parse import quote_plus

import httpx

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; LocalResearchScraper/1.0)"}


def _find_renderers(value):
    if isinstance(value, dict):
        if "videoRenderer" in value:
            yield value["videoRenderer"]
        for child in value.values():
            yield from _find_renderers(child)
    elif isinstance(value, list):
        for child in value:
            yield from _find_renderers(child)


def _runs_text(value: dict | None) -> str:
    if not value:
        return ""
    if "simpleText" in value:
        return value["simpleText"]
    return "".join(run.get("text", "") for run in value.get("runs", []))


async def _transcript(video_id: str) -> tuple[str | None, str | None]:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        api = YouTubeTranscriptApi()
        fetched = api.fetch(video_id, languages=["en"])
        return " ".join(piece.text for piece in fetched), None
    except Exception as exc:  # Captions commonly are disabled or unavailable.
        return None, f"YouTube transcript unavailable for {video_id}: {exc.__class__.__name__}"


async def _publication_date(client: httpx.AsyncClient, video_id: str) -> str | None:
    """Read the public watch-page metadata; search results only expose relative time."""
    try:
        response = await client.get(f"https://www.youtube.com/watch?v={video_id}")
        response.raise_for_status()
        match = re.search(r'"publishDate":"(\d{4}-\d{2}-\d{2})"', response.text)
        return match.group(1) if match else None
    except httpx.HTTPError:
        return None


async def search_youtube(company: str, ceo: str) -> tuple[list[dict], list[str]]:
    query = f'{ceo + " " if ceo else ""}{company} CEO interview artificial intelligence AI'
    url = "https://www.youtube.com/results?search_query=" + quote_plus(query)
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=18.0) as client:
            response = await client.get(url)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        return [], [f"YouTube search unavailable ({exc.__class__.__name__})"]
    match = re.search(r"var ytInitialData = (\{.*?\});</script>", response.text)
    if not match:
        return [], ["YouTube search response could not be read (possibly rate-limited)."]
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return [], ["YouTube search response contained invalid data."]
    videos, seen = [], set()
    for renderer in _find_renderers(payload):
        video_id = renderer.get("videoId")
        if not video_id or video_id in seen:
            continue
        seen.add(video_id)
        title = _runs_text(renderer.get("title"))
        if not title:
            continue
        description = _runs_text(renderer.get("detailedMetadataSnippets", [{}])[0].get("snippetText"))
        videos.append({
            "title": title,
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "published_at": _runs_text(renderer.get("publishedTimeText")) or None,
            "description": description or None,
            "publisher": _runs_text(renderer.get("ownerText")) or "YouTube",
            "source_type": "youtube",
            "content": None,
            "video_id": video_id,
        })
        if len(videos) == 8:
            break
    import asyncio
    video_ids = [video.pop("video_id") for video in videos]
    transcripts = await asyncio.gather(*[_transcript(video_id) for video_id in video_ids])
    async with httpx.AsyncClient(headers=HEADERS, timeout=18.0) as client:
        dates = await asyncio.gather(*[_publication_date(client, video_id) for video_id in video_ids])
    errors = []
    for video, (transcript, error), published_at in zip(videos, transcripts, dates):
        video["transcript"] = transcript
        video["published_at"] = published_at or video["published_at"]
        if error:
            errors.append(error)
    return videos, errors
