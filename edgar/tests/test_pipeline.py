"""Offline tests: seed the cache with fixture SEC responses so no network is needed."""
import csv
import gzip
import json
import random

import pandas as pd
import pytest

from edgar import __main__ as cli
from edgar.client import SecClient
from edgar.companies import CIK_LOOKUP_URL, TICKERS_URL, load_sec_names, match_companies, normalize
from edgar.filings import SUBMISSIONS_URL, SubmissionsSource
from edgar.keywords import find_terms, mentions_ai
from edgar.measure import measure_filing
from edgar.text import html_to_text, tag_sentences

TENK_HTML = """<html><body>
<div style="display:none"><ix:header>artificial intelligence hidden xbrl</ix:header></div>
<p>TABLE OF CONTENTS</p><p>Item 1. Business</p><p>Item 1A. Risk Factors</p><p>Item 7. Management's Discussion</p>
<p><b>Item 1. Business</b></p>
<p>We have deployed generative AI tools to 12,000 of our employees to automate customer service workflows.</p>
<p>Acme Inc. sells widgets across North America and Europe through distributors.</p>
<p><b>ITEM 1A. RISK FACTORS</b></p>
<p>The use of artificial intelligence may expose us to regulatory and reputational risks.</p>
<p>Patients receiving an aromatase inhibitor (AI) may experience side effects in our trials.</p>
<p><b>Item 7. Management&#8217;s Discussion and Analysis</b></p>
<p>During 2025 we invested $45.2 million in machine learning infrastructure and AI compute capacity.</p>
<p>We automatically renew certain contracts each year.</p>
<p><b>Item 9A. Controls and Procedures</b></p>
<p>We implemented robotic process automation in our financial close process.</p>
</body></html>"""


def seed(client: SecClient, url: str, body: bytes) -> None:
    path = client._cache_path(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(body))


@pytest.fixture
def client(tmp_path):
    return SecClient(cache_dir=tmp_path / "cache", offline=True)


def test_keyword_guards():
    assert not mentions_ai("SAID the AIRLINE MAINTAINS records.")
    assert not mentions_ai("an aromatase inhibitor (AI) regimen")
    assert not mentions_ai("We automatically renew contracts.")          # automation only
    assert not mentions_ai("Illinois Biometric Information Privacy Act")  # ambiguous, not in ai_total
    assert mentions_ai("Our A.I. platform") and mentions_ai("AI-driven pricing")
    # span de-duplication: RPA counted once, not also as process automation / automat*
    assert find_terms("robotic process automation").by_term == {"robotic process automation / RPA": 1}


def test_sections_and_measures():
    tagged = tag_sentences(html_to_text(TENK_HTML.encode()), "10-K")
    sections = {s for s, _ in tagged}
    assert {"business", "risk_factors", "mdna", "controls_procedures"} <= sections
    assert not any("hidden xbrl" in t for _, t in tagged)
    row, sents = measure_filing(tagged)
    assert row["ai_sentences"] == 3          # GenAI deploy, AI risk, ML investment (not aromatase, not automation-only)
    assert row["ai_in_business"] >= 1 and row["ai_in_risk_factors"] >= 1 and row["ai_in_mdna"] >= 2
    assert row["ai_spend_sentences"] == 1 and "$45.2 million" in row["ai_dollar_amounts"]
    assert row["n_automation"] >= 2          # RPA + "automatically" / "automate" captured separately
    by_sec = {s["section"]: s for s in sents}
    assert by_sec["business"]["adoption"] == 1
    assert by_sec["risk_factors"]["risk"] == 1


def test_normalize_compustat():
    assert normalize("ABERCROMBIE & FITCH  -CL A", compustat=True) == normalize("Abercrombie & Fitch Co /DE/")
    assert normalize("9F INC -ADR", compustat=True) == "9F"
    assert normalize("AMERICAN INTL GROUP", compustat=True) == normalize("AMERICAN INTERNATIONAL GROUP INC")


def test_match_and_filings(client, tmp_path):
    seed(client, TICKERS_URL, json.dumps({"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}).encode())
    seed(client, CIK_LOOKUP_URL, b"ABERCROMBIE & FITCH CO /DE/:0001018840:\nAMERICAN INTERNATIONAL GROUP INC:0000005272:\n"
                                 b"ZYVERSA THERAPEUTICS, INC.:0001859007:\nSOME LONG TRUNCATED COMPANY NAME HOLDINGS INC:0000000042:\n")
    firms = [{"company": "APPLE INC", "gvkey": "001690", "cik": ""},
             {"company": "ABERCROMBIE & FITCH  -CL A", "gvkey": "063643", "cik": ""},
             {"company": "AMERICAN INTL GROUP", "gvkey": "001487", "cik": ""},
             {"company": "SOME LONG TRUNCATED COMPA", "gvkey": "999999", "cik": ""},
             {"company": "NO SUCH FIRM", "gvkey": "000001", "cik": ""},
             {"company": "GIVEN CIK CO", "gvkey": "000002", "cik": "12345"}]
    rows = {r["gvkey"]: r for r in match_companies(firms, load_sec_names(client))}
    assert rows["001690"]["cik"] == "320193" and rows["001690"]["match_type"] == "exact"
    assert rows["063643"]["cik"] == "1018840"
    assert rows["001487"]["cik"] == "5272"
    assert rows["999999"]["match_type"] == "prefix" and rows["999999"]["cik"] == "42"
    assert rows["000001"]["match_type"] == "none" and rows["000001"]["needs_check"] == "yes"
    assert rows["000002"]["match_type"] == "given"

    sub = {"name": "ACME INC", "filings": {"recent": {
        "accessionNumber": ["0000000001-25-000001", "0000000001-25-000002", "0000000001-17-000003", "0000000001-25-000004"],
        "filingDate": ["2025-02-20", "2025-05-01", "2017-02-01", "2025-03-01"],
        "reportDate": ["2024-12-31", "2025-03-31", "2016-12-31", ""],
        "form": ["10-K", "10-Q", "10-K", "8-K"],
        "primaryDocument": ["acme-10k.htm", "acme-10q.htm", "old.htm", "8k.htm"]}, "files": []}}
    seed(client, SUBMISSIONS_URL.format(cik=1), json.dumps(sub).encode())
    fl = SubmissionsSource(client).filings("1", ["10-K", "10-Q"], "2018-01-01", "2026-12-31")
    assert [f.form for f in fl] == ["10-K", "10-Q"]
    assert fl[0].url == "https://www.sec.gov/Archives/edgar/data/1/000000000125000001/acme-10k.htm"


def test_end_to_end_cli(client, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "SecClient", lambda **kw: SecClient(cache_dir=client.cache_dir, offline=True))
    sub = {"name": "ACME INC", "filings": {"recent": {
        "accessionNumber": ["0000000001-25-000001"], "filingDate": ["2025-02-20"], "reportDate": ["2024-12-31"],
        "form": ["10-K"], "primaryDocument": ["acme-10k.htm"]}, "files": []}}
    seed(client, SUBMISSIONS_URL.format(cik=1), json.dumps(sub).encode())
    seed(client, "https://www.sec.gov/Archives/edgar/data/1/000000000125000001/acme-10k.htm", TENK_HTML.encode())
    comp = tmp_path / "company_cik.csv"
    comp.write_text("company,gvkey,cik,needs_check\nACME INC,000001,1,no\n")
    out = tmp_path / "out"
    cli.main(["run", str(comp), "--outdir", str(out), "--offline", "--workers", "1"])
    cli.main(["run", str(comp), "--outdir", str(out), "--offline", "--workers", "1"])  # resume: no duplicates
    filings = list(csv.DictReader((out / "filings.csv").open()))
    assert len(filings) == 1 and filings[0]["gvkey"] == "000001" and int(filings[0]["ai_total"]) > 0
    assert (out / "ai_sentences.csv").exists()
    cli.main(["panel", "--filings", str(out / "filings.csv"), "--out", str(out / "panel.csv")])
    panel = pd.read_csv(out / "panel.csv", dtype={"gvkey": str})
    assert panel.loc[0, "year"] == 2024 and panel.loc[0, "any_ai"] == 1


def test_regression_template(tmp_path):
    from edgar import regress
    random.seed(0)
    rows, outs = [], []
    for g in range(40):
        for y in range(2019, 2025):
            ai = random.random() * 5
            rows.append({"gvkey": f"{g:06d}", "year": y, "ai_per_10k_words": ai})
            outs.append({"gvkey": f"{g:06d}", "fyear": y, "ln_audit_fees": 10 + 0.3 * ai + g * 0.01 + random.gauss(0, 0.1),
                         "ln_assets": random.gauss(7, 1)})
    pd.DataFrame(rows).to_csv(tmp_path / "p.csv", index=False)
    pd.DataFrame(outs).to_csv(tmp_path / "o.csv", index=False)
    df = regress.load(str(tmp_path / "p.csv"), str(tmp_path / "o.csv"))
    res, n = regress.fit(df, "ln_audit_fees", "ai_per_10k_words", ["ln_assets"], ["firm", "year"], "gvkey")
    assert n == 240 and abs(res.params["ai_per_10k_words"] - 0.3) < 0.05


def test_shared_name_prefers_listed_registrant(client):
    seed(client, TICKERS_URL, json.dumps({"0": {"cik_str": 19617, "ticker": "JPM", "title": "JPMORGAN CHASE & CO"}}).encode())
    seed(client, CIK_LOOKUP_URL, b"JPMORGAN CHASE & CO:0001325347:\nJPMORGAN CHASE & CO:0000019617:\n"
                                 b"BANK OF AMERICA CORP /DE/:0000070858:\nBANK OF AMERICA CORP:0001085917:\n")
    rows = match_companies([{"company": "JPMORGAN CHASE & CO", "gvkey": "002968", "cik": ""},
                            {"company": "BANK OF AMERICA CORP", "gvkey": "007647", "cik": ""},
                            {"company": "ISHARES 0-5 YR HGH YLD CP BD", "gvkey": "000003", "cik": ""}], load_sec_names(client))
    assert rows[0]["cik"] == "19617" and rows[0]["needs_check"] == "no"     # listed registrant wins (not lexicographic "1325347")
    assert rows[1]["needs_check"] == "yes"                                   # neither listed in fixture -> flagged for --resolve
    assert rows[2]["likely_fund"] == "yes" and rows[0]["likely_fund"] == "no"
