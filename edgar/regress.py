"""Regression template: firm-year panel of AI measures -> outcome of interest.

The dependent variable isn't decided yet (candidates from the planning doc: audit fees,
internal-control weaknesses / changes in audit of IC, employee productivity, supply-chain
outcomes). Bring any firm-year file keyed by gvkey + year (e.g. a Compustat / Audit Analytics
export) and name the columns:

    python -m edgar.regress --panel data/edgar_out/panel_firm_year.csv \
        --outcomes audit_fees.csv --dv ln_audit_fees \
        --iv ai_per_10k_words --controls ln_assets leverage roa --fe firm year --cluster gvkey

Model:  DV_it = b * AI_it + g * Controls_it + firm FE + year FE + e_it,
        standard errors clustered by firm (the accounting-literature default).
IV options produced by `python -m edgar panel`:
  any_ai, ai_total, ln1p_ai_total, ai_per_10k_words, ai_spend_sentences, any_ai_spend,
  ai_adoption_sentences, n_genai, n_automation (Ashraf-style automation measure), ai_in_<section>
"""
from __future__ import annotations

import argparse

import pandas as pd
import statsmodels.formula.api as smf


def load(panel_path: str, outcomes_path: str) -> pd.DataFrame:
    panel = pd.read_csv(panel_path, dtype={"gvkey": str})
    out = pd.read_csv(outcomes_path, dtype={"gvkey": str})
    for d in (panel, out):
        d["gvkey"] = d["gvkey"].str.zfill(6)
        d.columns = [c.strip().lower() for c in d.columns]
    if "year" not in out.columns:
        for alt in ("fyear", "fiscal_year"):
            if alt in out.columns:
                out = out.rename(columns={alt: "year"})
    return panel.merge(out, on=["gvkey", "year"], how="inner")


def fit(df: pd.DataFrame, dv: str, iv: str, controls: list[str], fe: list[str], cluster: str | None):
    rhs = [iv, *controls] + [f"C({f if f != 'firm' else 'gvkey'})" for f in fe]
    data = df.dropna(subset=[dv, iv, *controls])
    formula = f"{dv} ~ " + " + ".join(rhs)
    kw = {}
    if cluster:
        kw = {"cov_type": "cluster", "cov_kwds": {"groups": pd.factorize(data[cluster])[0]}}
    return smf.ols(formula, data=data).fit(**kw), len(data)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--panel", required=True)
    p.add_argument("--outcomes", required=True, help="firm-year CSV with gvkey, year (or fyear), DV and controls")
    p.add_argument("--dv", required=True)
    p.add_argument("--iv", default="ai_per_10k_words")
    p.add_argument("--controls", nargs="*", default=[])
    p.add_argument("--fe", nargs="*", default=["firm", "year"], choices=["firm", "year"])
    p.add_argument("--cluster", default="gvkey")
    a = p.parse_args(argv)
    df = load(a.panel, a.outcomes)
    res, n = fit(df, a.dv, a.iv, a.controls, a.fe, a.cluster or None)
    keep = [a.iv, *a.controls]
    print(f"N = {n} firm-years, {df['gvkey'].nunique()} firms | FE: {', '.join(a.fe) or 'none'} | cluster: {a.cluster}")
    print(res.summary2().tables[1].loc[keep].round(4).to_string())
    print(f"R2 = {res.rsquared:.3f}")


if __name__ == "__main__":
    main()
