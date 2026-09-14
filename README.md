# CBAM Borrower Screening Panel

A local Streamlit app that screens a bank's corporate loan book for exposure to
the EU Carbon Border Adjustment Mechanism. Portfolio project by Musharraf Hassan.

**Status: in build.** Tasks 0 to 7 (configs and engine) are the current round.
The Streamlit UI, the synthetic portfolio generator and the portfolio roll-ups
come later. The full README is written in Task 12.

## What it does

For each borrower it produces a CBAM tier, an estimated certificate cost per year
from 2026 to 2035 under three carbon-price scenarios and two rule branches, a
materiality band from cost divided by EBITDA, a 2027 liquidity shock and a set of
risk flags. It then rolls those up across the portfolio.

## Important

- All demo data is **synthetic**. No real borrower data is used anywhere.
- Values that come from Commission proposals rather than adopted law are labelled
  **proposal** in config, code and UI.
- Materiality bands are the author's assumptions, not law.
- Not affiliated with any bank.

## Run

```
pip install -r requirements.txt
pytest
```

## Layout

See CLAUDE.md section 4.
