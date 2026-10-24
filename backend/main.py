from __future__ import annotations

import asyncio
import csv
import io
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .database import initialize, list_results, save_result
from .scraper import search_articles, search_youtube

app = FastAPI(title="CEO AI Research Scraper")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


class ScrapeRequest(BaseModel):
    company: str = Field(min_length=1, max_length=150)
    ceo: str = Field(default="", max_length=150)
    sources: list[Literal["youtube", "wsj", "bloomberg", "nyt", "economist", "forbes"]] = Field(min_length=1)


jobs: dict[str, dict] = {}


@app.on_event("startup")
def startup() -> None:
    initialize()


async def run_job(job_id: str, request: ScrapeRequest) -> None:
    job = jobs[job_id]
    company, ceo = request.company.strip(), request.ceo.strip()
    job.update(status="running", message="Searching selected public sources…", started_at=datetime.now(timezone.utc).isoformat())
    try:
        tasks = []
        if "youtube" in request.sources:
            tasks.append(search_youtube(company, ceo))
        article_sources = [source for source in request.sources if source != "youtube"]
        if article_sources:
            tasks.append(search_articles(company, ceo, article_sources))
        groups = await asyncio.gather(*tasks)
        raw = [item for results, _ in groups for item in results]
        job["errors"] = [error for _, errors in groups for error in errors]
        seen, stored = set(), 0
        for item in raw:
            if item["url"] in seen:
                continue
            seen.add(item["url"])
            item.update(company=company, ceo=ceo or None)
            stored += bool(save_result(item))
        job.update(status="completed", message=f"Finished: {stored} new sources saved.", found=len(raw), saved=stored)
    except Exception as exc:
        job.update(status="failed", message="Scrape stopped unexpectedly.", errors=job.get("errors", []) + [f"Unexpected error: {exc.__class__.__name__}"])


@app.post("/api/scrape")
async def scrape(request: ScrapeRequest):
    job_id = str(uuid.uuid4())
    jobs[job_id] = {"id": job_id, "status": "queued", "message": "Queued", "errors": [], "found": 0, "saved": 0}
    asyncio.create_task(run_job(job_id, request))
    return jobs[job_id]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404, "Scrape job not found")
    return jobs[job_id]


@app.get("/api/results")
def results(company: str | None = None):
    return list_results(company)


@app.get("/api/export.csv")
def export_csv(company: str | None = None):
    rows = list_results(company)
    output = io.StringIO()
    fields = ["company", "ceo", "source_type", "publisher", "title", "url", "published_at", "description", "content", "transcript", "created_at"]
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    filename = f"ceo-ai-research-{company or 'all'}.csv".replace(" ", "-")
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{filename}"'})
