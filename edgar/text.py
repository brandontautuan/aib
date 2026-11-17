"""Filing HTML/text -> clean text, split into sentences tagged with the filing section.

Section tagging matters for the research design: an AI mention in Risk Factors is often
boilerplate ("AI may create reputational risk"), while one in Business / MD&A / Use of
Proceeds is more likely to describe actual use or spending. Ashraf (2025) counts terms in
Item 9A (Controls and Procedures) specifically, so that section is tagged too.
"""
from __future__ import annotations

import re
import warnings

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# (regex on a heading line, section label). Order matters: more specific first.
TENK = [
    (r"item\s*1a\b", "risk_factors"), (r"item\s*1b\b", "unresolved_staff_comments"), (r"item\s*1c\b", "cybersecurity"),
    (r"item\s*1\b", "business"), (r"item\s*7a\b", "market_risk"), (r"item\s*7\b", "mdna"),
    (r"item\s*8\b", "financial_statements"), (r"item\s*9a\b", "controls_procedures"),
    (r"item\s*(?:2|3|4|5|6|9b|9c|9|1[0-6])\b", "other"),
]
TENQ = [
    (r"item\s*1a\b", "risk_factors"), (r"item\s*2\b.*(?:discussion|analysis)", "mdna"),
    (r"item\s*3\b", "market_risk"), (r"item\s*4\b.*control", "controls_procedures"),
    (r"item\s*1\b.*financial statements", "financial_statements"), (r"item\s*[2-6]\b", "other"),
]
TWENTYF = [
    (r"item\s*3\b", "risk_factors"), (r"item\s*4\b", "business"), (r"item\s*5\b", "mdna"),
    (r"item\s*15\b", "controls_procedures"), (r"item\s*(?:[1-2]|[6-9]|1[0-46-9])\b", "other"),
]
PROSPECTUS = [  # S-1 / F-1 / 424B4 use headings rather than items
    (r"^risk factors$", "risk_factors"), (r"^use of proceeds$", "use_of_proceeds"),
    (r"^management.s discussion and analysis", "mdna"), (r"^(?:our )?business$", "business"),
    (r"^prospectus summary$", "summary"), (r"^(?:management|executive compensation|underwriting|description of capital stock|"
                                            r"dilution|capitalization|dividend policy|principal stockholders)\b", "other"),
]
SECTION_MAPS = {"10-K": TENK, "10-Q": TENQ, "20-F": TWENTYF, "40-F": TWENTYF,
                "S-1": PROSPECTUS, "F-1": PROSPECTUS, "424B4": PROSPECTUS}

SENT_SPLIT = re.compile(r"(?<=[.!?])[\"”’)]*\s+(?=[\"“(]?[A-Z0-9])")
ABBREVS = re.compile(r"\b(?:Inc|Corp|Co|Ltd|No|U\.S|e\.g|i\.e|vs|approx|Mr|Ms|Dr|St|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.$")


def html_to_text(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    if "<html" not in text[:5000].lower() and "<document" not in text[:5000].lower():
        return text  # old plain-text filings
    soup = BeautifulSoup(text, "lxml")
    for tag in soup(["script", "style", "ix:header", "head"]):
        tag.decompose()
    for tag in soup.find_all(style=re.compile(r"display:\s*none", re.I)):
        tag.decompose()  # hidden inline-XBRL blocks
    for br in soup.find_all(["br", "p", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table"]):
        br.insert_after("\n")
    out = soup.get_text(" ")
    out = out.replace("\xa0", " ")
    out = re.sub(r"[ \t]+", " ", out)
    out = re.sub(r"\n\s*\n+", "\n", out)
    return out.strip()


def _sentences(block: str) -> list[str]:
    block = re.sub(r"\s+", " ", block).strip()
    parts, buf = [], ""
    for piece in SENT_SPLIT.split(block):
        buf = f"{buf} {piece}".strip() if buf else piece
        if ABBREVS.search(buf):
            continue  # "Inc." etc. -- not a real sentence end
        parts.append(buf)
        buf = ""
    if buf:
        parts.append(buf)
    return [p for p in parts if len(p) >= 25]


def tag_sentences(text: str, form: str) -> list[tuple[str, str]]:
    """Return (section, sentence). Lines that look like headings switch the current section.
    Table-of-contents entries also switch it briefly, but contain no sentences, so they don't matter."""
    smap = [(re.compile(p, re.I), lab) for p, lab in SECTION_MAPS.get(form.replace("/A", ""), TENK)]
    section, para, out = "front", [], []

    def flush():
        if para:
            out.extend((section, s) for s in _sentences(" ".join(para)))
            para.clear()

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        head = stripped.lower().rstrip(".:")
        if len(stripped) < 120 and (head.startswith("item") or form.replace("/A", "") in ("S-1", "F-1", "424B4")):
            label = next((lab for rx, lab in smap if rx.search(head)), None)
            # Prospectus headings must be short, standalone lines.
            if label and (head.startswith("item") or len(stripped) < 70):
                flush()
                section = label
                continue
        para.append(stripped)
        if stripped.endswith((".", "!", "?", ":")):
            flush()
    flush()
    return out
