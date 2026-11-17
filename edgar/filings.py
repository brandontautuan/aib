"""Find the filings to analyze for each CIK.

Two sources for the filing index (same JSON either way):
  - API:  https://data.sec.gov/submissions/CIK##########.json  (one request per company)
  - Bulk: submissions.zip  (the ZIP mentioned in the meeting; every company in one download,
          several GB; best when running thousands of companies)
"""
from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .client import SecClient

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:0>10}.json"
SUBMISSIONS_EXTRA_URL = "https://data.sec.gov/submissions/{name}"
BULK_SUBMISSIONS_URL = "https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/{doc}"

# 10-K/10-Q = US annual/quarterly; 20-F/40-F = foreign annual reports (many -ADR firms on the list);
# S-1/F-1 = IPO registration statements; 424B4 = final IPO prospectus.
DEFAULT_FORMS = ["10-K", "10-Q", "20-F", "40-F", "S-1", "F-1", "424B4"]


@dataclass
class Filing:
    cik: str
    company: str
    form: str
    filing_date: str
    report_date: str
    accession: str
    primary_doc: str

    @property
    def url(self) -> str:
        return ARCHIVE_URL.format(cik=int(self.cik), acc_nodash=self.accession.replace("-", ""), doc=self.primary_doc)


def _rows(block: dict) -> list[dict]:
    keys = ["accessionNumber", "filingDate", "reportDate", "form", "primaryDocument"]
    cols = [block.get(k, []) for k in keys]
    return [dict(zip(keys, vals)) for vals in zip(*cols)]


class SubmissionsSource:
    def __init__(self, client: SecClient | None = None, bulk_zip: Path | None = None):
        self.client = client
        self.zip = zipfile.ZipFile(bulk_zip) if bulk_zip else None
        self._names = set(self.zip.namelist()) if self.zip else set()

    def _load(self, name: str, url: str) -> dict | None:
        if self.zip:
            return json.loads(self.zip.read(name)) if name in self._names else None
        try:
            return json.loads(self.client.get(url))
        except Exception:
            return None

    def filings(self, cik: str, forms: list[str], start: str, end: str, include_amendments: bool = False) -> list[Filing]:
        main_name = f"CIK{int(cik):010d}.json"
        doc = self._load(main_name, SUBMISSIONS_URL.format(cik=int(cik)))
        if not doc:
            return []
        company = doc.get("name", "")
        rows = _rows(doc.get("filings", {}).get("recent", {}))
        for extra in doc.get("filings", {}).get("files", []):
            # Older history is paged into extra files; skip pages entirely outside the window.
            if extra.get("filingTo", "9999") < start or extra.get("filingFrom", "0000") > end:
                continue
            block = self._load(extra["name"], SUBMISSIONS_EXTRA_URL.format(name=extra["name"]))
            if block:
                rows += _rows(block)
        wanted = set(forms) | ({f + "/A" for f in forms} if include_amendments else set())
        out = []
        for r in rows:
            if r["form"] in wanted and start <= r["filingDate"] <= end and r["primaryDocument"]:
                out.append(Filing(cik=str(int(cik)), company=company, form=r["form"], filing_date=r["filingDate"],
                                  report_date=r.get("reportDate") or "", accession=r["accessionNumber"],
                                  primary_doc=r["primaryDocument"]))
        return sorted(out, key=lambda f: f.filing_date)
