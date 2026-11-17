"""SEC filing AI-keyword pipeline.

    python -m edgar keywords   --out keywords.csv
    python -m edgar match      "Company name.xlsx" --out data/company_cik.csv
    python -m edgar run        data/company_cik.csv --start 2018-01-01 --forms 10-K 10-Q S-1 [--limit 50] [--bulk submissions.zip]
    python -m edgar panel      --out data/panel_firm_year.csv
    python -m edgar fetch-bulk --out data/submissions.zip

See edgar/README.md for the full walkthrough.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .client import SecClient
from .companies import MATCH_FIELDS, load_company_list, load_sec_names, match_companies, resolve_ambiguous
from .filings import BULK_SUBMISSIONS_URL, DEFAULT_FORMS, Filing, SubmissionsSource
from .keywords import export_csv
from .measure import measure_filing
from .text import html_to_text, tag_sentences

DATA = Path("data")
FILING_META = ["gvkey", "cik", "company", "sec_name", "form", "filing_date", "report_date", "accession", "url"]
SENT_META = ["gvkey", "cik", "company", "form", "filing_date", "accession"]


def _append(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        if new:
            w.writeheader()
        w.writerows(rows)


def cmd_keywords(a):
    export_csv(a.out)
    print(f"Wrote {a.out}")


def cmd_match(a):
    companies = load_company_list(a.companies)
    client = SecClient(offline=a.offline)
    rows = match_companies(companies, load_sec_names(client), fuzzy_threshold=a.threshold)
    if a.resolve:
        n = sum(1 for r in rows if r["needs_check"] == "yes" and r["candidates"] and (a.include_funds or r["likely_fund"] == "no"))
        print(f"Resolving {n} ambiguous matches against SEC filing histories (~{n * 3 // 8 // 60 + 1} min)...")
        todo = [r for r in rows if a.include_funds or r["likely_fund"] == "no"]
        print(f"  resolved {resolve_ambiguous(todo, client)}")
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with a.out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=MATCH_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    from collections import Counter
    print(f"Wrote {a.out}: {len(rows)} companies", dict(Counter(r["match_type"] for r in rows)))
    ops = [r for r in rows if r["likely_fund"] == "no"]
    print(f"Excluding likely funds/ETFs ({len(rows) - len(ops)}): {len(ops)} operating companies,",
          f"{sum(1 for r in ops if r['cik'])} with a CIK, {sum(1 for r in ops if r['cik'] and r['needs_check'] == 'no')} confident")
    print("Rows with needs_check=yes should be hand-verified (or fill CIK from Compustat and re-run).")


def _process(client: SecClient, firm: dict, f: Filing) -> tuple[dict, list[dict]]:
    raw = client.get(f.url)
    tagged = tag_sentences(html_to_text(raw), f.form)
    row, sents = measure_filing(tagged)
    meta = {"gvkey": firm.get("gvkey", ""), "cik": f.cik, "company": firm.get("company", ""), "sec_name": f.company,
            "form": f.form, "filing_date": f.filing_date, "report_date": f.report_date, "accession": f.accession, "url": f.url}
    return {**meta, **row}, [{**{k: meta[k] for k in SENT_META}, **s} for s in sents]


def cmd_run(a):
    with a.companies.open(newline="", encoding="utf-8-sig") as fh:
        firms = [r for r in csv.DictReader(fh) if r.get("cik")]
    if a.only_confident:
        firms = [r for r in firms if r.get("needs_check", "no") == "no"]
    if not a.include_funds:
        firms = [r for r in firms if r.get("likely_fund", "no") == "no"]
    if a.limit:
        firms = firms[: a.limit]
    a.outdir.mkdir(parents=True, exist_ok=True)
    filings_csv, sents_csv, errors_csv = a.outdir / "filings.csv", a.outdir / "ai_sentences.csv", a.outdir / "errors.csv"
    done = set()
    if filings_csv.exists():
        with filings_csv.open(newline="", encoding="utf-8") as fh:
            done = {r["accession"] for r in csv.DictReader(fh)}

    client = SecClient(offline=a.offline)
    source = SubmissionsSource(client, bulk_zip=a.bulk)
    end = a.end or dt.date.today().isoformat()
    jobs = []
    for i, firm in enumerate(firms, 1):
        for f in source.filings(firm["cik"], a.forms, a.start, end, include_amendments=a.amendments):
            if f.accession not in done:
                jobs.append((firm, f))
        if i % 100 == 0:
            print(f"indexed {i}/{len(firms)} companies, {len(jobs)} filings to fetch", file=sys.stderr)
    print(f"{len(firms)} companies -> {len(jobs)} new filings to process ({len(done)} already done)")

    ok = 0
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        futures = {pool.submit(_process, client, firm, f): (firm, f) for firm, f in jobs}
        for n, fut in enumerate(as_completed(futures), 1):
            firm, f = futures[fut]
            try:
                row, sents = fut.result()
                _append(filings_csv, [row])
                _append(sents_csv, sents)
                ok += 1
            except Exception as exc:  # keep going; failures are logged for a re-run
                _append(errors_csv, [{"cik": f.cik, "accession": f.accession, "url": f.url, "error": repr(exc)[:300]}])
            if n % 25 == 0:
                print(f"  {n}/{len(jobs)} filings", file=sys.stderr)
    print(f"Done: {ok} processed, {len(jobs) - ok} errors. Outputs in {a.outdir}/")


def cmd_panel(a):
    import pandas as pd
    df = pd.read_csv(a.filings, dtype={"gvkey": str, "cik": str})
    period = df["report_date"].fillna("").where(df["report_date"].fillna("") != "", df["filing_date"])
    df["year"] = period.str[:4].astype(int)
    df["doc_type"] = df["form"].str.replace("/A", "", regex=False).map(
        {"10-K": "annual", "20-F": "annual", "40-F": "annual", "10-Q": "quarterly",
         "S-1": "ipo", "F-1": "ipo", "424B4": "ipo"}).fillna("other")
    sums = [c for c in df.columns if c.startswith(("n_", "ai_in_")) or c in
            ("total_words", "ai_total", "ai_sentences", "ai_spend_sentences", "ai_adoption_sentences", "ai_risk_sentences")]
    g = df.groupby(["gvkey", "cik", "year"], dropna=False)
    panel = g[sums].sum()
    panel["n_filings"] = g.size()
    panel["any_ai"] = (panel["ai_total"] > 0).astype(int)
    panel["ai_per_10k_words"] = (panel["ai_total"] / panel["total_words"].where(panel["total_words"] > 0) * 1e4).round(3)
    panel["ln1p_ai_total"] = (panel["ai_total"] + 1).apply(__import__("math").log).round(4)
    panel["any_ai_spend"] = (panel["ai_spend_sentences"] > 0).astype(int)
    panel["had_ipo_filing"] = g["doc_type"].apply(lambda s: int((s == "ipo").any()))
    panel["company"] = g["company"].first()
    panel = panel.reset_index()
    panel.to_csv(a.out, index=False)
    print(f"Wrote {a.out}: {len(panel)} firm-years, {panel['gvkey'].nunique()} firms")


def cmd_fetch_bulk(a):
    SecClient().download(BULK_SUBMISSIONS_URL, a.out)
    print(f"Saved {a.out}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m edgar", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("keywords", help="export the AI keyword list")
    s.add_argument("--out", type=Path, default=Path("ai_keywords.csv"))
    s.set_defaults(func=cmd_keywords)

    s = sub.add_parser("match", help="map company names/GVKEYs to SEC CIKs")
    s.add_argument("companies", type=Path)
    s.add_argument("--out", type=Path, default=DATA / "company_cik.csv")
    s.add_argument("--threshold", type=int, default=93)
    s.add_argument("--offline", action="store_true", help="use cached SEC name files only")
    s.add_argument("--resolve", action="store_true", help="look up filing histories to pick the right CIK when names are shared")
    s.add_argument("--include-funds", action="store_true", help="also resolve rows flagged likely_fund")
    s.set_defaults(func=cmd_match)

    s = sub.add_parser("run", help="download filings and measure AI mentions")
    s.add_argument("companies", type=Path, help="output of `match` (needs cik; gvkey/company optional)")
    s.add_argument("--forms", nargs="+", default=DEFAULT_FORMS)
    s.add_argument("--start", default="2018-01-01")
    s.add_argument("--end", default=None)
    s.add_argument("--amendments", action="store_true", help="also include 10-K/A, S-1/A, ...")
    s.add_argument("--limit", type=int, default=0, help="first N companies only (pilot runs)")
    s.add_argument("--only-confident", action="store_true", help="skip rows flagged needs_check")
    s.add_argument("--include-funds", action="store_true", help="keep rows flagged likely_fund (skipped by default)")
    s.add_argument("--bulk", type=Path, default=None, help="local submissions.zip instead of per-company API calls")
    s.add_argument("--workers", type=int, default=4)
    s.add_argument("--outdir", type=Path, default=DATA / "edgar_out")
    s.add_argument("--offline", action="store_true")
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("panel", help="aggregate filings.csv to a firm-year panel for regressions")
    s.add_argument("--filings", type=Path, default=DATA / "edgar_out" / "filings.csv")
    s.add_argument("--out", type=Path, default=DATA / "edgar_out" / "panel_firm_year.csv")
    s.set_defaults(func=cmd_panel)

    s = sub.add_parser("fetch-bulk", help="download SEC's bulk submissions.zip (several GB)")
    s.add_argument("--out", type=Path, default=DATA / "submissions.zip")
    s.set_defaults(func=cmd_fetch_bulk)

    a = p.parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()
