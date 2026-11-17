"""Map the team's company list (Compustat names + GVKEY) to SEC CIKs.

Best option: pull CIK straight from Compustat (WRDS comp.company has a `cik` column) and
fill the empty CIK column in the company file -- rows that already have a CIK are used as-is.

Fallback (this module): match Compustat names to every name EDGAR has ever recorded
(cik-lookup-data.txt, which includes former names and dead firms), plus current
tickers/names (company_tickers.json). Each row gets a match_type and score so
low-confidence matches can be hand-checked:
  given    CIK came from the input file
  exact    normalized names identical
  prefix   Compustat name is a truncation of the SEC name (Compustat cuts names at ~28 chars)
  fuzzy    rapidfuzz token_sort_ratio >= threshold (CHECK THESE)
  none     no match
"""
from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from pathlib import Path

from .client import SecClient

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
CIK_LOOKUP_URL = "https://www.sec.gov/Archives/edgar/cik-lookup-data.txt"

# Compustat appends share-class / status suffixes after " -", e.g. "ABERCROMBIE & FITCH  -CL A", "9F INC -ADR".
COMPUSTAT_SUFFIX = re.compile(r"\s+-\s*[A-Z0-9 .]{1,12}$")
LEGAL = {"INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COMPANY", "LTD", "LIMITED", "LLC", "LP", "PLC",
         "SA", "NV", "AG", "SE", "AB", "ASA", "SPA", "BV", "HOLDINGS", "HLDGS", "HLDG", "HLDNGS", "HOLDING", "GROUP", "GRP", "THE",
         "DE", "NEW", "OLD", "ADR", "ADS", "TRUST", "TR", "CP", "SPN", "REDH"}
ABBREV = {"INTL": "INTERNATIONAL", "TECH": "TECHNOLOGIES", "TECHNOLOGY": "TECHNOLOGIES", "SYS": "SYSTEMS",
          "PHARMA": "PHARMACEUTICALS", "PHARMACEUTICAL": "PHARMACEUTICALS", "SVCS": "SERVICES", "MGMT": "MANAGEMENT",
          "COMMUN": "COMMUNICATIONS", "COMM": "COMMUNICATIONS", "FINL": "FINANCIAL", "BANCORPORATION": "BANCORP",
          "ENTMT": "ENTERTAINMENT", "INDS": "INDUSTRIES", "MTRS": "MOTORS", "AMER": "AMERICA", "NATL": "NATIONAL",
          "RES": "RESOURCES", "PPTYS": "PROPERTIES", "THERAP": "THERAPEUTICS", "THER": "THERAPEUTICS",
          "THERAPEUTIC": "THERAPEUTICS", "ACQ": "ACQUISITION", "ACQSTN": "ACQUISITION", "ACQUISITI": "ACQUISITION",
          "GLBL": "GLOBAL", "INCM": "INCOME", "BANCP": "BANCORP", "BANCSHS": "BANCSHARES", "INVESTMT": "INVESTMENT",
          "INVT": "INVESTMENT", "INVS": "INVESTMENTS", "MUN": "MUNICIPAL", "MUNI": "MUNICIPAL", "IDX": "INDEX", "INDX": "INDEX",
          "EQ": "EQUITY", "MED": "MEDICAL", "PHARM": "PHARMACEUTICALS", "ENGY": "ENERGY", "ENRG": "ENERGY",
          "ENTPR": "ENTERPRISES", "ENTRPRS": "ENTERPRISES", "MTG": "MORTGAGE", "SVC": "SERVICES", "PRODS": "PRODUCTS",
          "CMNTY": "COMMUNITY", "SOLTNS": "SOLUTIONS", "SOLUTN": "SOLUTIONS", "ELECTR": "ELECTRONICS", "LABS": "LABORATORIES",
          "BIOSCI": "BIOSCIENCES", "CAP": "CAPITAL", "DEV": "DEVELOPMENT", "TECHNOLOGES": "TECHNOLOGIES",
          "TECHNOLOGS": "TECHNOLOGIES", "TECHS": "TECHNOLOGIES", "SCI": "SCIENCES", "HLTH": "HEALTH", "HLTHCR": "HEALTHCARE",
          "MGT": "MANAGEMENT", "PETE": "PETROLEUM", "PETRO": "PETROLEUM", "AUTOMOTV": "AUTOMOTIVE", "COMMUNICTNS": "COMMUNICATIONS",
          "COMMUNS": "COMMUNICATIONS", "INFO": "INFORMATION", "INS": "INSURANCE", "SYSTM": "SYSTEMS", "NETWRKS": "NETWORKS",
          "&": "AND"}

# Funds/ETFs/trusts don't file 10-K/10-Q, so they can't get filing-based AI measures; flagged for the team to drop.
FUND_RE = re.compile(
    r"\b(?:FUND|FD|FDS|ETF|ETFS|ETN|MUNICIPAL|MUNI|MUN|INDEX|IDX|INDX|ISHARES|PROSHARES|PROSHRS|SPDR|XTRACKERS|"
    r"VICTORYSHRS|GLOBAL X|"
    r"CURRENCYSHARES|DIREXION|GRANITESHARES|LEVERAGED|VOLATILITY|VLTY|2X|3X|"
    r"INCOME (?:FUND|FD|TR|TRUST|OPP\w*|SEC\w*|STRAT\w*|SHS)|INCM|ARBITRAGE|BUFFER|BFFR|"
    r"(?:INVESCO|WISDOMTREE|VANGUARD|NUVEEN|PIMCO|CALAMOS|ALLIANZGI|ALLIANZIM|MACKENZIE|ABERDEEN|ABRDN|BLACKROCK|"
    r"FIDELITY|FRANKLIN|EATON VANCE|JOHN HANCOCK|COHEN & STEERS|GABELLI|SPROTT|21SHARES)\b.*\b(?:FD|FUND|TR|TRUST|"
    r"INCOME|INCM|ETF|STRATEGY|STRAT|OPP\w*|EQ\w*|GLBL|GLOBAL|MUNI\w*|CREDIT|DIVIDEND|DIV|HIGH YIELD|HI|SHS|SWED|"
    r"YEN|EURO|FRANC|POUND|DOLLAR|CRPT|CRYPTO|BITCOIN|ETHER\w*)|\b(?:CLOSED[- ]END|INTERVAL|BDC)\b)\b")


def normalize(name: str, compustat: bool = False) -> str:
    s = (name or "").upper()
    if compustat:
        s = COMPUSTAT_SUFFIX.sub("", s)
    s = re.sub(r"/[A-Z]{2,3}/?$", "", s.strip())        # EDGAR state tags like "APPLE INC /CA/"
    s = s.replace("&", " AND ")
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    tokens = [ABBREV.get(t, t) for t in s.split()]
    tokens = [t for t in tokens if t not in LEGAL]
    return " ".join(tokens)


def load_company_list(path: Path) -> list[dict]:
    """Read the team's list (.xlsx or .csv). Needs a name column; GVKEY/CIK optional."""
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook
        rows = list(load_workbook(path, read_only=True).active.iter_rows(values_only=True))
        header = [str(h or "").strip().lower() for h in rows[0]]
        records = [dict(zip(header, r)) for r in rows[1:]]
    else:
        with path.open(newline="", encoding="utf-8-sig") as f:
            records = [{k.strip().lower(): v for k, v in r.items()} for r in csv.DictReader(f)]
    out = []
    for r in records:
        name = str(r.get("company name") or r.get("company") or r.get("conm") or "").strip()
        if not name or name.lower() == "grand total":
            continue
        gv = r.get("gvkey")
        cik = r.get("cik")
        out.append({
            "company": name,
            "gvkey": str(int(float(gv))).zfill(6) if gv not in (None, "") else "",
            "cik": str(int(float(cik))) if cik not in (None, "") else "",
        })
    return out


def light(name: str, compustat: bool = False) -> str:
    """Punctuation-only normalization (keeps INC/COMPANY...) for matching Compustat's truncated names."""
    s = (name or "").upper()
    if compustat:
        s = COMPUSTAT_SUFFIX.sub("", s)
    s = re.sub(r"/[A-Z]{2,3}/?$", "", s.strip()).replace("&", " AND ")
    return " ".join(re.sub(r"[^A-Z0-9 ]+", " ", s).split())


class SecNames(dict):
    """normalized name -> set of CIKs; .light holds punctuation-only names -> CIKs;
    .listed holds CIKs that are current exchange-listed registrants (company_tickers.json)."""
    def __init__(self):
        super().__init__()
        self.light: dict[str, set[str]] = defaultdict(set)
        self.listed: set[str] = set()

    def best(self, ciks) -> str:
        """Several EDGAR entities can share a name (parent vs. subsidiary/old shell). Prefer the
        currently listed registrant; otherwise the lowest CIK (older registrants), and flag it."""
        ciks = sorted(set(ciks), key=int)
        listed = [c for c in ciks if c in self.listed]
        return (listed or ciks)[0]

    def add(self, name: str, cik: str) -> None:
        n = normalize(name)
        if n:
            self.setdefault(n, set()).add(cik)
        self.light[light(name)].add(cik)


def load_sec_names(client: SecClient) -> SecNames:
    """All historical EDGAR names (incl. former names, dead firms) + current tickers."""
    index = SecNames()
    tickers = json.loads(client.get(TICKERS_URL))
    for row in tickers.values():
        index.add(row["title"], str(row["cik_str"]))
        index.listed.add(str(row["cik_str"]))
    # Lines look like "APPLE INC:0000320193:" (names may themselves contain colons).
    for line in client.get(CIK_LOOKUP_URL).decode("latin-1").splitlines():
        parts = line.rstrip(":").rsplit(":", 1)
        if len(parts) == 2 and parts[1].strip().isdigit():
            index.add(parts[0], str(int(parts[1])))
    return index


def match_companies(companies: list[dict], sec_index: SecNames, fuzzy_threshold: int = 93) -> list[dict]:
    from rapidfuzz import fuzz, process

    by_first: dict[str, list[str]] = defaultdict(list)  # block searches on first token for speed
    for n in sec_index:
        by_first[n.split()[0]].append(n)
    light_first: dict[str, list[str]] = defaultdict(list)
    for n in sec_index.light:
        if n:
            light_first[n.split()[0]].append(n)

    out = []
    for c in companies:
        row = {**c, "normalized": normalize(c["company"], compustat=True), "match_type": "none",
               "matched_sec_name": "", "score": "", "candidates": ""}
        key = row["normalized"]
        if c["cik"]:
            row.update(match_type="given", score=100)
        elif key in sec_index:
            ciks = sorted(sec_index[key], key=int)
            row.update(cik=sec_index.best(ciks), match_type="exact", matched_sec_name=key, score=100,
                       candidates=";".join(ciks) if len(ciks) > 1 else "")
        elif key:
            pool = by_first.get(key.split()[0], [])
            lkey = light(c["company"], compustat=True)
            # Compustat truncates long names (~25-28 chars) mid-word: "...TRUNCATED COMPA".
            pref = [n for n in light_first.get(lkey.split()[0], []) if n.startswith(lkey)] if len(lkey) >= 20 else []
            if pref:
                best = min(pref, key=len)
                ciks = sorted({k for n in pref for k in sec_index.light[n]}, key=int)
                row.update(cik=sec_index.best(sec_index.light[best]), match_type="prefix", matched_sec_name=best, score=95,
                           candidates=";".join(ciks) if len(ciks) > 1 else "")
            elif pool:
                hit = process.extractOne(key, pool, scorer=fuzz.token_sort_ratio, score_cutoff=fuzzy_threshold)
                if hit:
                    row.update(cik=sec_index.best(sec_index[hit[0]]), match_type="fuzzy", matched_sec_name=hit[0],
                               score=round(hit[1]), candidates=";".join(sorted(sec_index[hit[0]], key=int)))
        cands = row["candidates"].split(";") if row["candidates"] else []
        # Ambiguous only if more than one candidate and the pick isn't the single listed registrant.
        ambiguous = len(cands) > 1 and sum(c in sec_index.listed for c in cands) != 1
        row["needs_check"] = "yes" if row["match_type"] in ("fuzzy", "none") or ambiguous else "no"
        row["likely_fund"] = "yes" if FUND_RE.search(c["company"].upper()) else "no"
        out.append(row)
    return out


def resolve_ambiguous(rows: list[dict], client: SecClient, forms=("10-K", "10-Q", "20-F", "40-F")) -> int:
    """For rows still flagged with several candidate CIKs, look each candidate up and keep the one
    that actually files periodic reports (most 10-K/10-Q/20-F filings). One request per candidate."""
    from .filings import SUBMISSIONS_URL
    fixed = 0
    for row in rows:
        cands = row["candidates"].split(";") if row.get("candidates") else []
        if row.get("needs_check") != "yes" or len(cands) < 2 or row["match_type"] == "given":
            continue
        counts = {}
        for c in cands:
            try:
                doc = json.loads(client.get(SUBMISSIONS_URL.format(cik=int(c))))
            except Exception:
                continue
            recent = doc.get("filings", {}).get("recent", {})
            counts[c] = sum(f in forms for f in recent.get("form", []))
        filers = [c for c, n in counts.items() if n > 0]
        if filers:
            row["cik"] = max(filers, key=lambda c: counts[c])
            row["resolved_by"] = "most periodic filings: " + ", ".join(f"{c}={counts[c]}" for c in cands if c in counts)
            if len(filers) == 1 and row["match_type"] != "fuzzy":
                row["needs_check"] = "no"
            fixed += 1
    return fixed


MATCH_FIELDS = ["company", "gvkey", "cik", "match_type", "score", "matched_sec_name", "candidates", "needs_check",
                "likely_fund", "resolved_by", "normalized"]
