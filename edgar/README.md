# SEC filings → AI keyword measures

Built from the Nov 2026 meeting plan: YouTube is set aside, and AI activity is measured from SEC filings
(10-K, 10-Q, and IPO filings: S-1 / F-1 / 424B4; also 20-F/40-F for the foreign `-ADR` firms on the list).
Output is a firm-year panel that feeds straight into a regression.

```
company list (GVKEY) ──match──▶ CIK ──run──▶ filings.csv + ai_sentences.csv ──panel──▶ firm-year panel ──regress──▶ results
```

## Setup (once)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install httpx beautifulsoup4 lxml openpyxl pandas rapidfuzz statsmodels
export SEC_USER_AGENT="SJSU AI Research your.name@sjsu.edu"   # SEC requires a name + contact email
```

## Steps

| Step | Command | Output |
|---|---|---|
| 1. Keyword list | `python -m edgar keywords --out ai_keywords.csv` | the dictionary below, one row per term |
| 2. Company → CIK | `python -m edgar match "Company name.xlsx"` | `data/company_cik.csv` with `match_type`, `score`, `needs_check` |
| 3. Measure filings | `python -m edgar run data/company_cik.csv --start 2018-01-01` | `data/edgar_out/filings.csv`, `ai_sentences.csv`, `errors.csv` |
| 4. Firm-year panel | `python -m edgar panel` | `data/edgar_out/panel_firm_year.csv` |
| 5. Regression | `python -m edgar.regress --panel ... --outcomes ... --dv ...` | coefficient table |

Useful flags for `run`: `--limit 50` (pilot), `--forms 10-K S-1`, `--only-confident` (skip unverified CIK matches),
`--bulk data/submissions.zip` (use SEC's bulk ZIP instead of per-company API calls; get it with
`python -m edgar fetch-bulk`), `--workers 4`. Runs are **resumable**: re-running skips filings already in
`filings.csv`, and every download is cached in `data/edgar_cache/`.

### Scale
The list has **19,588** Compustat companies (many delisted/foreign). At SEC's ~8 requests/sec,
one 10-K per firm-year for 2018–2025 is roughly 100k filings (~4–6 hours plus caching disk space);
adding 10-Qs roughly quadruples that. Pilot with `--limit`, then filter to the final sample before a full run.

## The keyword dictionary (`keywords.py`)

Every term records its source so the measure is defensible:
**Ashraf (2025, RAST)** (the 10-K/10-Q list in our lit review), **Babina et al. (2024, JFE)** AI-skill terms,
and **team additions** for post-2022 generative-AI vocabulary.

| Category | Examples | In `ai_total`? |
|---|---|---|
| `core_ai` | artificial intelligence, AI (case-sensitive), AI-powered/-driven, machine learning, deep learning, neural network, cognitive computing | yes |
| `genai` | generative AI, LLM, foundation model, ChatGPT/GPT-4, copilot, AI agents/agentic, chatbot, RAG, OpenAI/Anthropic | yes |
| `ai_methods` | NLP, computer vision, image/facial/speech recognition, virtual agent/assistant, reinforcement learning | yes |
| `ai_tools` | TensorFlow/PyTorch, random forest/gradient boosting, AI infrastructure/compute/GPU | yes |
| `automation` | RPA, intelligent/process automation, autonomous, `automat*` catch-all | **separate measure** (`n_automation`) |
| ambiguous | biometric, predictive analytics, data science | counted in `n_ambiguous_not_counted` only |

False-positive guards: "AI" is case-sensitive and skipped in sentences about aromatase inhibitors,
American Indian, avian influenza, etc. Overlapping matches are counted once ("robotic process automation"
is not also counted as "process automation" and "automat*").

## What the outputs mean

**`filings.csv`** (one row per filing): `ai_total`, `any_ai`, `ai_per_10k_words`, counts per category,
counts per section (`ai_in_business`, `ai_in_risk_factors`, `ai_in_mdna`, `ai_in_controls_procedures`,
`ai_in_use_of_proceeds`), and sentence counts by flag.

**`ai_sentences.csv`** (one row per AI sentence, for hand-coding): the sentence, its section, matched terms, and
- `spend`: money/investment language (+ extracted `$` amounts). This is the closest thing to **AI-specific
  spending**: firms almost never report an "AI spend" line item, so the dollar figures have to come from
  sentences like "we invested $45 million in machine-learning infrastructure."
- `adoption`: the firm describes its own use ("we deployed…", "our employees…", workflows, productivity).
- `risk`: Risk Factors section or risk/regulatory language, so likely boilerplate rather than real use.

This mirrors the "dedicated vs. PR" split from the CEO-statement task: `ai_adoption_sentences` and
`ai_spend_sentences` outside Risk Factors are the "dedicated" signals; risk-section mentions are the cheap-talk end.

**`panel_firm_year.csv`**: summed by gvkey × year (fiscal period from `report_date`), plus `ln1p_ai_total`,
`any_ai_spend`, and `had_ipo_filing`, ready to merge with Compustat/Audit Analytics on `gvkey, year`.

## Regression template (`regress.py`)
`DV = b·AI + controls + firm FE + year FE`, SEs clustered by firm. The DV is still open (audit fees, ICW,
productivity, supply chain…); pass any firm-year CSV with `gvkey` and `year`/`fyear`.

## Known limits
- **CIK matching**: names are matched against every name EDGAR has recorded. Check rows with
  `needs_check = yes`. More reliable: export `cik` from WRDS `comp.company` into the empty CIK column, and
  those rows are used as-is.
- Section detection is heading-based; unusual layouts fall back to `other` (counts are still correct).
- The keyword measure counts mentions, not dollars; validate a random sample of `ai_sentences.csv` by hand
  before relying on the flags.
