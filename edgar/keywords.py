"""AI keyword dictionary + matcher.

Each keyword records where it came from so the measure can be defended in the paper:
  - Ashraf (2025, RAST) "Does automation improve financial reporting?" -- 10-K/10-Q word list
  - Babina, Fedyk, He & Hodson (2024, JFE; WP 2022) -- AI-skill terms from job postings
  - Team addition -- post-2022 generative-AI vocabulary not in the older lists

Categories (each becomes its own count column so the team can choose which measure to use):
  core_ai     general AI vocabulary                      -> included in the main AI measure
  genai       generative AI / LLM era terms              -> included in the main AI measure
  ai_methods  AI subfields and applications              -> included in the main AI measure
  ai_tools    technical tooling (Babina et al.)          -> included in the main AI measure
  automation  Ashraf's broader automation terms           -> SEPARATE measure (very noisy alone)

`main=True` marks terms that count toward ai_total. Ambiguous terms are kept but excluded
from ai_total (main=False) and reported separately, so nothing is silently dropped.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

ASHRAF = "Ashraf (2025, RAST)"
BABINA = "Babina et al. (2024, JFE)"
TEAM = "Team addition (GenAI era)"


@dataclass(frozen=True)
class Keyword:
    term: str                 # human-readable label
    pattern: str              # regex
    category: str
    source: str
    main: bool = True         # counts toward ai_total
    case_sensitive: bool = False
    note: str = ""


K = Keyword
KEYWORDS: list[Keyword] = [
    # ---- core_ai -----------------------------------------------------------------------
    K("artificial intelligence", r"artificial[- ]intelligen\w*", "core_ai", ASHRAF),
    K("AI (acronym)", r"(?<![\w.-])A\.?I\.?(?!\w|\.\w)", "core_ai", TEAM, case_sensitive=True,
      note="Case-sensitive. Skipped in sentences that use AI for other things (aromatase inhibitor, American Indian, etc.)."),
    K("AI-<adjective> (AI-powered, AI-driven, AI-enabled, AI-based, AI-first)",
      r"\bAI[- ](?:powered|driven|enabled|based|first|native|assisted|generated|related|ready)\b", "core_ai", TEAM, case_sensitive=True),
    K("AI tech / AI technology", r"\bAI[- ]tech\w*", "core_ai", ASHRAF),
    K("AI related", r"\bAI[- ]related\b", "core_ai", ASHRAF),
    K("conversational AI", r"\bconversational AI\b", "core_ai", ASHRAF),
    K("evolutionary AI / computing", r"\bevolutionary (?:AI|comput\w+)", "core_ai", ASHRAF),
    K("intelligent system", r"\bintelligent systems?\b", "core_ai", ASHRAF),
    K("cognitive computing", r"\bcognitive comput\w+", "core_ai", ASHRAF),
    K("machine learning", r"\bmachine[- ]learn\w*", "core_ai", ASHRAF),
    K("deep learning", r"\bdeep[- ]learn\w*", "core_ai", ASHRAF),
    K("neural network", r"\bneural[- ]net\w*", "core_ai", ASHRAF),

    # ---- genai -------------------------------------------------------------------------
    K("generative AI", r"\bgenerative (?:AI|artificial intelligence|models?)\b|\bGen[- ]?AI\b", "genai", TEAM),
    K("large language model / LLM", r"\blarge language models?\b|\bLLMs?\b", "genai", TEAM),
    K("foundation model", r"\bfoundation(?:al)? models?\b", "genai", TEAM),
    K("transformer model", r"\btransformer(?:-based)? (?:models?|architecture)\b", "genai", TEAM),
    K("GPT / ChatGPT", r"\bChat ?GPT\b|\bGPT-?\d(?:\.\d)?o?\b", "genai", TEAM),
    K("copilot", r"\bcopilots?\b", "genai", TEAM, note="Mostly Microsoft/GitHub Copilot; occasionally aviation."),
    K("AI agent / agentic", r"\bAI agents?\b|\bagentic\b", "genai", TEAM),
    K("AI assistant", r"\bAI assistants?\b", "genai", TEAM),
    K("chatbot", r"\bchat ?bots?\b", "genai", TEAM),
    K("retrieval-augmented generation", r"\bretrieval[- ]augmented generation\b|\bRAG\b(?= (?:pipeline|system|model))", "genai", TEAM),
    K("prompt engineering", r"\bprompt engineer\w*", "genai", TEAM),
    K("AI vendors (OpenAI, Anthropic)", r"\bOpenAI\b|\bAnthropic\b", "genai", TEAM,
      note="Vendor names; signal adoption/partnership. Gemini/Claude excluded as too ambiguous."),

    # ---- ai_methods --------------------------------------------------------------------
    K("natural language processing / NLP", r"\bnatural[- ]language[- ]process\w*|\bNLP\b", "ai_methods", ASHRAF),
    K("natural language understanding / generation", r"\bnatural[- ]language (?:understanding|generation)\b", "ai_methods", TEAM),
    K("computer vision", r"\bcomputer vision\b|\bmachine vision\b", "ai_methods", ASHRAF),
    K("image recognition", r"\bimage recognition\b", "ai_methods", ASHRAF),
    K("facial recognition", r"\bfac(?:ial|e) recognition\b", "ai_methods", ASHRAF),
    K("speech / voice recognition", r"\b(?:speech|voice) recognition\b", "ai_methods", ASHRAF),
    K("virtual agent", r"\bvirtual agents?\b", "ai_methods", ASHRAF),
    K("virtual assistant", r"\bvirtual assistants?\b", "ai_methods", ASHRAF),
    K("reinforcement learning", r"\breinforcement learning\b", "ai_methods", BABINA),
    K("supervised / unsupervised learning", r"\b(?:un|semi-)?supervised learning\b", "ai_methods", BABINA),
    K("predictive model / predictive analytics", r"\bpredictive (?:model\w*|analytics)\b", "ai_methods", TEAM, main=False,
      note="Often traditional statistics; reported separately, not in ai_total."),
    K("biometric", r"\bbiometric\w*", "ai_methods", ASHRAF, main=False,
      note="In Ashraf's list but in filings is mostly privacy law (BIPA) risk language; kept out of ai_total."),

    # ---- ai_tools (Babina et al. AI-skill terms) -----------------------------------------
    K("TensorFlow / PyTorch / Keras / scikit-learn", r"\b(?:TensorFlow|PyTorch|Keras|scikit-learn|Hugging ?Face)\b", "ai_tools", BABINA),
    K("ML algorithms (random forest, gradient boosting, SVM)",
      r"\brandom forests?\b|\bgradient boost\w*|\bsupport vector machines?\b", "ai_tools", BABINA),
    K("AI infrastructure / compute / GPU", r"\bAI (?:infrastructure|compute|computing|chips?|accelerators?|data cent(?:er|re)s?)\b|\bGPUs?\b",
      "ai_tools", TEAM, note="GPU mentions can be gaming/graphics for hardware makers; check by industry."),
    K("data science / data scientist", r"\bdata scien\w+", "ai_tools", BABINA, main=False,
      note="Broad; reported separately."),

    # ---- automation (Ashraf; separate measure) ---------------------------------------------
    K("robotic process automation / RPA", r"\brobotic process automation\b|\bRPA\b", "automation", ASHRAF, case_sensitive=False),
    K("intelligent automation", r"\bintelligent automation\b", "automation", ASHRAF),
    K("process automation", r"\bprocess automation\b", "automation", ASHRAF),
    K("marketing automation", r"\bmarketing automation\b", "automation", ASHRAF),
    K("automation solutions", r"\bautomation solutions?\b", "automation", ASHRAF),
    K("autonomous tech / autonomous", r"\bautonomous\w*", "automation", ASHRAF,
      note="Very noisy (autonomous region, autonomous vehicles)."),
    K("automat* (catch-all)", r"\bautomat\w*", "automation", ASHRAF,
      note="Ashraf's catch-all; captures automation/automate/automatically. Use only as the automation measure."),
]

# Sentences where "AI" means something else; the acronym match is skipped there.
AI_ACRONYM_FALSE_CONTEXTS = re.compile(
    r"aromatase inhibitor|american indian|artificial insemination|avian influenza|"
    r"amnesty international|adequate intake|\bAI Ltd\b|ai(?:r)? india", re.IGNORECASE)

CATEGORIES = ["core_ai", "genai", "ai_methods", "ai_tools", "automation"]
_COMPILED = [(k, re.compile(k.pattern, 0 if k.case_sensitive else re.IGNORECASE)) for k in KEYWORDS]


@dataclass
class Hits:
    by_term: dict[str, int] = field(default_factory=dict)

    def add(self, term: str, n: int) -> None:
        self.by_term[term] = self.by_term.get(term, 0) + n

    def total(self, main_only: bool = True, categories: list[str] | None = None) -> int:
        lookup = {k.term: k for k in KEYWORDS}
        return sum(n for t, n in self.by_term.items()
                   if (not main_only or lookup[t].main) and (categories is None or lookup[t].category in categories))


def find_terms(sentence: str) -> Hits:
    """Count keyword hits in one sentence. Overlapping terms are deduplicated by span
    so e.g. 'AI-powered' is not also counted as 'AI', and 'robotic process automation'
    is not also counted as 'process automation' and 'automat*'."""
    hits = Hits()
    taken: list[tuple[int, int]] = []
    ai_false = bool(AI_ACRONYM_FALSE_CONTEXTS.search(sentence))
    # Longer patterns first so specific phrases claim their span before generic ones.
    for kw, rx in sorted(_COMPILED, key=lambda p: -len(p[0].pattern)):
        if kw.term == "AI (acronym)" and ai_false:
            continue
        n = 0
        for m in rx.finditer(sentence):
            s, e = m.span()
            if any(s < te and ts < e for ts, te in taken):
                continue
            taken.append((s, e))
            n += 1
        if n:
            hits.add(kw.term, n)
    return hits


def mentions_ai(sentence: str) -> bool:
    return find_terms(sentence).total(main_only=True, categories=["core_ai", "genai", "ai_methods", "ai_tools"]) > 0


def export_csv(path: Path) -> None:
    with path.open("w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["term", "category", "counts_toward_ai_total", "source", "case_sensitive", "regex", "note"])
        for k in KEYWORDS:
            w.writerow([k.term, k.category, "yes" if k.main and k.category != "automation" else "no",
                        k.source, "yes" if k.case_sensitive else "no", k.pattern, k.note])
