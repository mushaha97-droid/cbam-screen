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

## Web dashboard

`webapp/` is the same screening outputs as a static page. Open it and you are
using the tool: no install, no login, no Python running behind it.

```
cd webapp
python -m http.server 8765
```

It reads the exported csv star schema, and it computes nothing of its own. Every
cost, band, flag and cash flow on screen came out of the engine, and the page
only sums, ranks and draws them. It ships with the fixture export, and the
FIXTURE banner on every screen comes from `meta.csv`, not from the page. Drag in
your own export folder on the Your own data tab and the banner changes with it.
Those files are read in the browser and are not uploaded anywhere.

See **[webapp/README.md](webapp/README.md)**.

## Power BI track

The same screening outputs are also delivered as a Power BI dashboard. Power BI
Desktop is a graphical tool, so this repo owns everything about that dashboard
that can be text and leaves only the visual assembly to Desktop:

- `src/export_powerbi.py` runs the engine and writes a star schema of csv files.
- `powerbi/cbam-screen.SemanticModel/` is the semantic model, hand-written as
  TMDL, with every table, relationship and measure readable in a diff.
- `powerbi/theme/cbam-theme.json` is the report theme.
- `docs/POWERBI.md` has the visual-by-visual page blueprints.

Build the fixture export and open the project:

```
python -m src.export_powerbi \
    --borrowers data/powerbi_fixture/borrowers_fixture.csv \
    --config-dir tests/fixtures \
    --prices data/powerbi_fixture/prices_fixture.yaml \
    --out data/powerbi_fixture/export
```

Everything in that export is **fixture data**: thirteen invented borrowers and a
flat invented carbon price. The carbon prices and the real sector mapping are
both gated on sources that could not be fetched, and neither is filled by
guessing. `docs/POWERBI.md` says exactly what unlocks each one.

Full instructions, the four page blueprints, the colour rules and an honest note
on which parts of the file format could not be confirmed from documentation are
all in **[docs/POWERBI.md](docs/POWERBI.md)**.

## Layout

See CLAUDE.md section 4.
