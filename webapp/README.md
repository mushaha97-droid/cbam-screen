# The web dashboard

The screening outputs as a page you can open in a browser. No install, no login,
no Python running behind it.

`index.html`, `app.js`, `styles.css` and a folder of csv files. That is the whole
thing. There is no build step and no dependency, so it can be served by any
static host, or opened from a folder with one command.

## What it is, and what it is not

The Python engine in `src/` is the single source of truth. It computes every
cost, band, flag and cash flow, and `src/export_powerbi.py` writes the answers
out as a star schema of csv files.

This page reads those files and adds up, ranks, filters and draws them. It
contains no cost formula, no band boundary, no threshold and no carbon price. If
a number on screen is not a value from the export, or a sum, a count, a minimum,
a maximum or a rank over those values, that is a bug.

That is also why the page can be trusted offline: change a rule in `config/` and
the number here changes only when the exporter has been run again, which is the
right way round.

## Run it

```
cd webapp
python -m http.server 8765
```

Then open `http://127.0.0.1:8765/`. Any static file server will do. Opening
`index.html` straight from the filesystem does not work, because a browser
refuses to fetch the csv files from a `file://` page. Use the Your own data tab
in that case and select the csv files by hand.

## The data it ships with

`webapp/data/` is a copy of `data/powerbi_fixture/export/`, which is the fixture
export: thirteen invented borrowers and a flat invented carbon price. The banner
says FIXTURE on every screen, and it says that because `meta.csv` says
`data_label,FIXTURE`, not because the word is written into the page. Export a
different file set and the banner changes with it.

Refresh the copy after re-running the exporter:

```
python -m src.export_powerbi \
    --borrowers data/powerbi_fixture/borrowers_fixture.csv \
    --config-dir tests/fixtures \
    --prices data/powerbi_fixture/prices_fixture.yaml \
    --out data/powerbi_fixture/export
cp data/powerbi_fixture/export/*.csv webapp/data/
```

## Using your own borrower list

1. Run the exporter against your own borrower csv, which needs the seven
   required columns in CLAUDE.md section 5. Give it `--data-label UPLOADED`.
2. Open the page, go to the Your own data tab, and drag in every csv file from
   the export folder, or select them with the file picker.
3. Read the validation report: which files were found, how many rows each has,
   and which data label was detected.

The files are read in the browser tab. Nothing is uploaded anywhere, and the page
makes no network request other than the one for its fonts.

## The ten files it reads

Eight are required. Without one of them the page keeps showing the file set that
was already open and says which one was missing.

| File | What the page does with it |
| --- | --- |
| `dim_borrower.csv` | the borrower list, exposure, and what the bank supplied |
| `dim_scenario.csv` | the scenario selector and its default |
| `dim_branch.csv` | the branch selector, its default and its adopted or proposal status |
| `dim_year.csv` | the year selector and the liquidity shock year |
| `fact_cost.csv` | cost, band, tier label, price and free allocation per borrower, year, scenario and branch |
| `fact_liquidity.csv` | the payment windows behind the cash-out chart |
| `fact_flags.csv` | which flags are active, and the engine's reason for each |
| `meta.csv` | the data label, the banner, the price source and the flag reference point |

Two are optional. The page works without them and says what is missing rather
than filling the gap:

| File | What is lost without it |
| --- | --- |
| `fact_cost_line.csv` | the formula box on the borrower detail view, which prints the cost with this borrower's own tonnage, emission factor, price and free allocation share |
| `questions.csv` | the client questions, leaving only the engine's own reason per flag |

## Accessibility and print

Tabs are a real tablist with arrow-key navigation. Every clickable cell is a
button, so the whole page works from the keyboard. Colour is never the only
carrier of meaning: a band cell always prints its letter as well as its
background. The Summary view has a print stylesheet, so Print or save as PDF
gives a one-page committee handout on white paper with the data label on it.
