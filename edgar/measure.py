"""Per-filing AI measures + AI sentences for hand review.

Filing-level output (one row per filing -> regression panel):
  total_words, ai_total (main AI terms), per-category counts, automation_total,
  ai_per_10k_words, ai_sentences, ai_spend_sentences, ai_adoption_sentences,
  ai_risk_sentences, ai_dollar_amounts, any_ai (0/1), and ai counts by section.

Sentence-level output (one row per AI-mentioning sentence):
  section, the sentence, matched terms, flags:
    spend     -- money/investment language in the same sentence (+ extracted $ amounts)
    adoption  -- the company describes its own use (we use / deployed / our employees / workflows)
    risk      -- risk/uncertainty language or Risk Factors section (likely boilerplate)
The flags are deliberately transparent cue-word rules so they can be hand-validated.
"""
from __future__ import annotations

import re
from collections import Counter

from .keywords import CATEGORIES, KEYWORDS, find_terms

SPEND_RE = re.compile(
    r"\binvest(?:ed|ing|ment|ments)?\b|\bcapital expenditures?\b|\bcapex\b|\bspen(?:d|ding|t)\b|\bexpend\w*|"
    r"\bbudget\w*|\ballocat\w+|\bacqui(?:red?|sition)s?\b|\bpurchas\w+|\bresearch and development\b|\bR&D\b|"
    r"\bcosts? (?:of|related to|associated with)\b|\bproceeds\b|\$\s?\d", re.I)
DOLLAR_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:million|billion|thousand|[MBK])\b)?", re.I)
ADOPTION_RE = re.compile(
    r"\bwe (?:use|used|utilize|leverage|have (?:deployed|implemented|integrated|adopted|developed|launched|built)|"
    r"deployed|implemented|integrated|adopted|developed|launched|built|are (?:using|deploying|implementing|integrating))\b|"
    r"\bour (?:employees|workforce|associates|engineers|operations|processes|workflows?|internal)\b|"
    r"\binternal(?:ly)?\b|\bworkflows?\b|\bproductivity\b|\befficienc\w+|\bautomat(?:e|ed|ing) (?:our|certain|manual)\b", re.I)
RISK_RE = re.compile(
    r"\brisks?\b|\bcould (?:adversely|harm|negatively)\b|\bmay (?:not|adversely|harm|be subject)\b|\buncertain\w*|"
    r"\bregulat\w+|\bliabilit\w+|\breputational\b|\bthere can be no assurance\b", re.I)
WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]+")

AI_CATS = [c for c in CATEGORIES if c != "automation"]
SECTIONS = ["business", "risk_factors", "mdna", "controls_procedures", "use_of_proceeds", "other"]
_TERM = {k.term: k for k in KEYWORDS}


def measure_filing(tagged: list[tuple[str, str]]) -> tuple[dict, list[dict]]:
    words = 0
    cat_counts = Counter()
    sec_counts = Counter()
    term_counts = Counter()
    flags = Counter()
    dollars: list[str] = []
    sentences = []
    for section, sent in tagged:
        words += len(WORD_RE.findall(sent))
        hits = find_terms(sent)
        if not hits.by_term:
            continue
        for term, n in hits.by_term.items():
            kw = _TERM[term]
            term_counts[term] += n
            cat_counts[kw.category] += n
            if not kw.main:
                cat_counts["ambiguous_not_counted"] += n
        ai_n = hits.total(main_only=True, categories=AI_CATS)
        if ai_n == 0:
            continue  # automation-only / ambiguous-only sentences are counted but not listed
        sec_counts[section if section in SECTIONS else "other"] += ai_n
        spend = bool(SPEND_RE.search(sent))
        amounts = DOLLAR_RE.findall(sent) if spend else []
        adoption = bool(ADOPTION_RE.search(sent))
        risk = section == "risk_factors" or bool(RISK_RE.search(sent))
        flags.update(ai=1, spend=spend, adoption=adoption, risk=risk)
        dollars += amounts
        sentences.append({
            "section": section, "sentence": sent[:2000], "ai_terms": "; ".join(sorted(hits.by_term)),
            "ai_term_count": ai_n, "spend": int(spend), "dollar_amounts": "; ".join(amounts),
            "adoption": int(adoption), "risk": int(risk),
        })
    ai_total = sum(n for t, n in term_counts.items() if _TERM[t].main and _TERM[t].category in AI_CATS)
    row = {
        "total_words": words,
        "ai_total": ai_total,
        "any_ai": int(ai_total > 0),
        "ai_per_10k_words": round(ai_total / words * 10000, 3) if words else 0.0,
        **{f"n_{c}": cat_counts.get(c, 0) for c in CATEGORIES},
        "n_ambiguous_not_counted": cat_counts.get("ambiguous_not_counted", 0),
        "ai_sentences": flags["ai"],
        "ai_spend_sentences": flags["spend"],
        "ai_adoption_sentences": flags["adoption"],
        "ai_risk_sentences": flags["risk"],
        "ai_dollar_amounts": "; ".join(dict.fromkeys(dollars)),
        **{f"ai_in_{s}": sec_counts.get(s, 0) for s in SECTIONS},
        "top_terms": "; ".join(f"{t} ({n})" for t, n in term_counts.most_common(5)),
    }
    return row, sentences
