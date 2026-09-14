# Power BI track

The screening outputs, delivered as a Power BI dashboard.

Power BI Desktop is a graphical tool. Nothing built by clicking in it can be
reviewed in a diff, so this repo owns everything that can be text and leaves
only the visual assembly to Desktop. That split is the whole design:

- `src/export_powerbi.py` runs the engine and writes a star schema of csv files.
- `powerbi/cbam-screen.SemanticModel/` is the semantic model, written by hand as
  TMDL, with every table, relationship and measure in plain text.
- `powerbi/theme/cbam-theme.json` is the report theme.
- This file is the page blueprint. It says what each visual is, which fields go
  in it, and which rules colour it.

You open the project, set one parameter, refresh, and build the pages from the
blueprints below.

---

## 1. Before anything, read the label

Every number you will see on screen is **fixture data**. There are thirteen
invented borrowers and a flat, invented carbon price of 100, 80 and 60 euro per
tonne. None of it is a forecast, a bank's book, or anyone's exposure.

That is not a placeholder to be quietly replaced later. It is the honest state
of the project: two inputs are gated and neither can be filled by guessing. See
section 7, "The gates", for exactly what unlocks real numbers and where.

The label travels with the data. `meta.csv` carries `data_label`, the price
source and the sha256 of every config file that produced the export, and the
`Data Banner` measure turns those into one line of text. Put that banner on
every page.

---

## 2. Opening the project

### What you need

Power BI Desktop, current version, with two preview features switched on under
`File > Options and settings > Options > Preview features`:

- **Power BI Project (.pbip) save option**
- **Store semantic model using TMDL format**

Both were still preview features in the Microsoft documentation as of the
research done for this track. There is no published minimum version number for
either, so the honest instruction is "current Desktop with both boxes ticked"
rather than a version you can check. If the project does not open, the first
thing to check is those two boxes, and the second is that Desktop is up to date.

Note the separate thing that is **not** preview: the TMDL **view** inside
Desktop, which is the code editor, is generally available. That is a different
feature from the on-disk TMDL project format, and a lot of writing on the
internet conflates them.

### Open it

1. Open `powerbi/cbam-screen.pbip` from Power BI Desktop.
2. Go to `Home > Transform data > Edit parameters`.
3. Set **DataFolder** to the full path of the export folder on your machine. In
   this repo that folder is `data/powerbi_fixture/export`, so on the machine
   this was written on the value is:

   ```
   C:\Users\Mush\Desktop\projects\cbam-screen\data\powerbi_fixture\export
   ```

   It has to be an absolute path. Power Query has no idea where the project file
   sits, so it cannot resolve anything relative to it. A trailing backslash is
   fine, the queries trim it.
4. `Home > Refresh`.

All eight tables load from csv in import mode. There is no gateway, no database
and no credential to enter. If Desktop asks about file access, it is asking for
permission to read a local folder.

### If Desktop refuses to open the project

The semantic model is the part of this delivery that was checked hardest. The
report folder is the part that was checked least, because the shape of a Power
BI report on disk is documented for its containers but not for the contents of
a visual. If Desktop complains when opening `cbam-screen.pbip`, do this:

1. Delete `powerbi/cbam-screen.Report/` and `powerbi/cbam-screen.pbip`.
2. In Desktop, create a blank report and use `File > Save as > Power BI project
   (.pbip)`, saving into `powerbi/` with the name `cbam-screen`.
3. Close Desktop.
4. Replace the generated `cbam-screen.SemanticModel/definition/` folder with the
   one from this repo, and replace `definition.pbism` too.
5. Reopen the project.

That keeps everything this repo authored and throws away only the empty canvas.

### Changing the model after it is open

Desktop does not reliably notice TMDL files that changed on disk while it was
running. Close Desktop, edit the file, reopen. The Microsoft documentation is
self-contradictory on this point, so assume a restart is needed.

---

## 3. What the model contains

### Tables

| Table | Grain | Rows in the fixture export |
| --- | --- | --- |
| `dim_borrower` | one borrower | 13 |
| `dim_scenario` | one carbon-price scenario | 3 |
| `dim_branch` | one rule branch, adopted or proposal | 2 |
| `dim_year` | one year, 2026 to 2036 | 11 |
| `fact_cost` | borrower, year, scenario, branch | 780 |
| `fact_liquidity` | borrower, scenario, branch, payment | 1497 |
| `fact_flags` | borrower, flag | 117 |
| `meta` | key and value | 37 |
| `_Measures` | no data, measures only | 0 |

Two grain decisions are worth knowing before you build anything.

**Tier depends on the branch.** A downstream borrower is out of scope under
adopted law and Tier 3 under the extension proposal. `dim_borrower[tier]` is the
tier under one branch only, named in `meta` as `tier_branch_for_dim_borrower`,
and it exists so that a simple exposure breakdown works. Whenever a branch
slicer is on the page, use `fact_cost[tier_label_plain]` on the axis and the
`Exposure by Tier` measure, which reads the borrower set back off the fact.

**Flags are evaluated at one reference point.** `RATING_GAP` and `MONITOR` both
depend on the cost, and the cost depends on a year, a scenario and a branch. The
export picks one such point, writes it onto every row of `fact_flags` and into
`meta`, and does not join the table to `dim_year`. So the year slicer does not
change the flag chips, which is correct, and the `Flag Reference Point` measure
tells the reader which year they are looking at. In the fixture export that
point is 2027, Delayed Transition, adopted.

`fact_liquidity` is deliberately not a full cross join. A borrower that owes
nothing in every year has no payment rows at all, because a zero payment row
would make the liquidity page look like it had something to say.

### The five plain-language tier labels

CLAUDE.md section 9 forbids showing a tier number anywhere a label exists. The
labels travel with the data, in `tier_label_plain`:

| Tier | Label |
| --- | --- |
| 1 | Makes CBAM goods in the EU |
| 2 | Imports CBAM goods |
| 3 | May be caught from 2028 |
| 4 | Pays via input prices |
| 0 | Not affected |

The numeric `tier` column exists only to sort those labels, and it is set as the
sort column in TMDL. Do not put it on a visual.

### Reading a zero in `fact_cost`

`cost_basis` says what a row rests on, and you should show it wherever a cost of
zero can appear:

| `cost_basis` | What a zero means |
| --- | --- |
| `direct_obligation` | a real cost, not zero |
| `below_mass_threshold` | nothing owed, the tonnage is under the threshold |
| `lost_allocation` | a real cost, the free allocation withdrawn this year |
| `lost_allocation_no_production` | not computed: the sector has no production intensity in config |
| `input_share_uplift_unavailable` | not computed: needs `portfolio.py`, which does not exist yet |
| `out_of_scope` | nothing owed |

The two "not computed" cases are real gaps, not tidy zeros, and the difference
matters. `input_share_uplift_unavailable` covers every Tier 4 borrower until
CLAUDE.md Task 9 is done.

### Measures

All twenty-six live in `_Measures`. Each one carries a description in the model
naming the config entry its inputs came from, which is the CLAUDE.md section 7
traceability rule applied to DAX. You can read those descriptions in Desktop in
the Model view.

| Measure | What it is |
| --- | --- |
| `Total Cost EUR` | sum of certificate cost across whatever is in view |
| `Cost (Selected Scenario and Branch)` | cost for exactly one scenario and branch, falling back to the defaults recorded in `meta` |
| `Materiality Ratio` | cost divided by EBITDA, blank where EBITDA is not positive |
| `Band` | the band the engine assigned, or "mixed" when more than one is in view |
| `Band Label` | the band written out, for example "3 to 6 percent of EBITDA" |
| `Exposure EUR` | exposure of the borrowers in view, as the bank supplied it |
| `Exposure by Tier` | exposure of the borrowers a fact filter implies, so tier by branch works |
| `Exposure Carrying RATING_GAP` | exposure of borrowers whose CBAM band sits well above the bank's own rating |
| `Top 20 Rank by Ratio` | rank by cost to EBITDA, worst first, among the borrowers left by the filters |
| `Cash-Out EUR` | cash leaving the borrower in the years in view |
| `2027 Cash-Out` | cash out in the shock year, which is read from `meta`, not written into the DAX |
| `Cash-Out % of Working Capital` | blank where no working capital was supplied |
| `Best Case Cost EUR` | lowest cost across every scenario and branch combination |
| `Worst Case Cost EUR` | highest cost across the same |
| `Best Case Scenario and Branch` | which combination produced the best case |
| `Worst Case Scenario and Branch` | which combination produced the worst case |
| `Flag Count` | how many flags fired |
| `Borrowers with Any Flag` | how many borrowers carry at least one |
| `Flags Active` | the active flag codes for one borrower, comma separated |
| `% Borrowers Estimated` | share of borrowers with an input filled from a sector average |
| `Borrower Count` | borrowers in view |
| `Data Label` | FIXTURE, SYNTHETIC or UPLOADED |
| `Data Banner` | the one-line banner every page carries |
| `Price Source` | the file the carbon prices came from |
| `Generated At` | when the export ran, in UTC |
| `Flag Reference Point` | the year, scenario and branch the flags were evaluated at |
| `Why This Rating` | the formula written out with one borrower's own numbers in it |
| `What To Ask This Client` | one line per active flag, with the engine's own reason |

Three of them need a number that is really config: the liquidity shock year and
the default scenario and branch. None writes that number into the DAX. They read
it from `meta`, which the exporter wrote from config. The band boundaries are
absent from the DAX for the same reason, and a test fails if one appears.

### Format strings

Money measures use `"€"#,##0`, which shows exact euro. That is right for the
fixture portfolio, where the whole hand example is 3,800 euro and thousands
formatting would show it as 4. On a real portfolio of 300 borrowers, switch the
money measures to `"€"#,##0,` which divides by a thousand and shows euro
thousands. It is a one-line edit per measure in `_Measures.tmdl`, and the column
headers should then say "EUR thousands".

---

## 4. The theme

`powerbi/theme/cbam-theme.json`. Import it in Desktop with `View > Themes >
Browse for themes`.

**One canvas commitment: light.** Power BI reports are opened on a white
background far more often than a dark one, print to white, and paste into decks
that are white. A theme that hedges between the two looks wrong in both. So the
canvas is white, the page outspace is a very light grey, and the portfolio
identity survives in the ink rather than the paper:

| Role | Colour | Where it is used |
| --- | --- | --- |
| Deep forest | `#0B1512` | text, and the fill behind the banner textbox |
| Forest green | `#0F5132` | first data colour, table accent, links, the "good" sentiment |
| Amber | `#E0B061` | flags and warnings, the "neutral" sentiment |
| Deep red | `#B42318` | the "bad" sentiment |

The bright accent `#4ADE80` from the portfolio identity is not used as a data
colour. It does not have enough contrast on white to be read as a bar or a line.
It is fine as a small highlight on the dark banner if you want it there.

**Colourblind safety.** Two rules do the work:

- The first four data colours differ in lightness as well as hue, so a small
  multiple chart still separates when hue is unavailable. A test checks this.
- The conditional formatting ramp for materiality bands is a single hue running
  light to dark. Ordering is carried by lightness alone, and colour only
  reinforces it. A green to amber to red ramp would have been the obvious choice
  and would have been the wrong one, because green and amber are the classic
  pair that a reader with deuteranopia cannot separate.

**The band ramp**, five steps, light to dark, for the exposure heatmap:

| Band | Colour | Meaning |
| --- | --- | --- |
| L | `#E6EFEA` | below 1 percent of EBITDA |
| ML | `#B8D4C4` | 1 to 3 percent |
| M | `#7FB39A` | 3 to 6 percent |
| MH | `#3F7D63` | 6 to 12 percent |
| H | `#12402F` | above 12 percent |

Use white text on MH and H, `#0B1512` on L, ML and M. Always print the band
letter in the cell as well as colouring it, so the matrix survives a greyscale
print and a colourblind reader.

The theme's `minimum`, `center` and `maximum` are set to the ends and middle of
that ramp, so Power BI's own gradient conditional formatting lands in the same
family without you entering hex codes by hand.

---

## 5. The four pages

These map CLAUDE.md Task 10 into Power BI terms. Build them in Desktop.

Every page carries the same two things at the top, so a screenshot of any page
can never lose its label:

- **Banner textbox**, full width, 32 px tall, background `#0B1512`, text white,
  bound to the `Data Banner` measure. Reads for example: "FIXTURE DATA. Carbon
  prices: fixture_not_a_forecast. Exported 2026-09-14T20:51:42Z from engine
  commit 8cc4949. Not any bank's actual exposure. Materiality bands are the
  author's assumptions, not law."
- **Page title**, left aligned, text class `title`.

A textbox cannot bind directly to a measure in older Desktop builds. If yours
cannot, use a Card visual with no category, title off, and the measure in
Fields. The result looks the same.

### Page 1: Portfolio

The page a risk manager opens first. It answers "where is this book exposed".

**Slicers**, in a row under the banner, all set to single select:

| Slicer | Field | Default |
| --- | --- | --- |
| Scenario | `dim_scenario[scenario_label]` | Delayed Transition |
| Branch | `dim_branch[branch_label]` | Adopted law |
| Year | `dim_year[year_label]`, filtered to `is_obligation_year` is true | 2027 |

Set the defaults by selecting them and then `View > Bookmarks > Add`, named
"Default view", with Data ticked, and set it as the page's landing bookmark.
`dim_scenario[is_default]` and `dim_branch[is_default]` mark which values those
are, so the defaults come from the export rather than from memory.

Put a small card beside the branch slicer showing `dim_branch[status]`. When the
reader picks the proposal branch it reads "proposal", which is the CLAUDE.md
section 2 requirement that a proposal is labelled wherever it drives a number.

**Visual 1, the heatmap.** Matrix.

- Rows: `fact_cost[tier_label_plain]`
- Columns: `fact_cost[band]`
- Values: `Exposure by Tier`
- Conditional formatting on the value cell: background colour by rules, one rule
  per band using the five colours in section 4. Rules, not a gradient, because
  the band is a category and a gradient would imply the gaps between bands are
  equal, which they are not.
- Turn the column subtotal on and the row subtotal on. The grand total is the
  exposure of every borrower in view.
- Cell text: the exposure figure. Add `Borrower Count` as a second value if the
  committee wants counts as well as euro.

**Visual 2, the batch view.** Table. This is the "run all scenarios and branches
and show best and worst per tier" toggle from CLAUDE.md Task 10, built as a
permanent table rather than a toggle, because a toggle hides the range by
default and the range is the point.

- Rows: `fact_cost[tier_label_plain]`
- Values, in order: `Best Case Cost EUR`, `Best Case Scenario and Branch`,
  `Worst Case Cost EUR`, `Worst Case Scenario and Branch`
- The two text columns are there so no number appears without its assumptions.
  The worst case will usually name the proposal branch.
- These measures ignore the scenario and branch slicers by design. Put a
  subtitle on the visual saying "across all three scenarios and both branches".

**Visual 3, the RATING_GAP card.** Card.

- Field: `Exposure Carrying RATING_GAP`
- Subtitle textbox underneath: "Exposure where the CBAM band sits at least two
  bands above the bank's own transition rating. The gap parameter is in
  config/thresholds.yaml. Flags evaluated at: " and then the `Flag Reference
  Point` measure.
- Give it the amber accent `#E0B061` as a left border or a title underline. Do
  not colour the number itself red, because the number is not a loss.

**Visual 4, what changed against your own rating.** Table.

- Rows: `dim_borrower[name]`, `dim_borrower[bank_transition_rating]`, `Band`,
  `Exposure EUR`
- Filter: `fact_flags[flag]` is RATING_GAP and `fact_flags[active]` is true
- Sort by `Exposure EUR` descending.

**Visual 5, data quality strip.** Three cards in a row at the bottom:
`Borrower Count`, `% Borrowers Estimated`, `Flag Count`.

### Page 2: Borrowers

The working list. An analyst filters it, reads it and exports it.

**Table visual**, full width, with these columns in this order:

| Column | Field or measure | Note |
| --- | --- | --- |
| Borrower | `dim_borrower[name]` | |
| What it is | `fact_cost[tier_label_plain]` | never the tier number |
| Band | `Band` | conditional background from the band ramp |
| Cost | `Cost (Selected Scenario and Branch)` | |
| Cost to EBITDA | `Materiality Ratio` | |
| 2027 cash out | `2027 Cash-Out` | |
| Cash out vs working capital | `Cash-Out % of Working Capital` | blank is honest, do not fill it |
| Flags | `Flags Active` | |
| Estimated | `dim_borrower[estimated]` | |
| Exposure | `dim_borrower[exposure_eur]` | |

Conditional formatting:

- **Band** cell background from the five-colour ramp, by rules on the text
  value, with the letter shown.
- **Estimated** cell: an amber `#E0B061` background where true. This is the
  chip that says the row is indicative.
- **Flags**: Power BI has no chip control. Two options that work. Either leave
  `Flags Active` as text and set the font to a smaller size, or add one small
  column per flag with a conditional icon. The text column is less work and
  reads better when a borrower has four flags.

**Filter pane**, expanded and pinned, with: `dim_borrower[tier_label_plain]`,
`fact_cost[band]`, `dim_borrower[declarant_status]`,
`dim_borrower[supplier_data]`, `dim_borrower[country]`, `fact_flags[flag]`.

**Export note.** A textbox under the table, not a tooltip, because a tooltip
does not survive a screenshot:

> Use the visual's own "Export data" menu to take this list to csv. Power BI
> exports what the filters left, which is the point. The exported file carries
> no data label, so write the banner line at the top of it before it goes in a
> credit file.

That last sentence matters. A csv that leaves Power BI loses its provenance, and
a fixture number in a credit file with no label on it is the worst outcome this
whole project is designed to avoid.

### Page 3: Borrower detail

One borrower at a time. Reached by drill-through from the Borrowers table.

Set up drill-through with `dim_borrower[borrower_id]` in the "Add drill-through
fields here" well, and keep all filters on.

**Header.** Card row: borrower name, `fact_cost[tier_label_plain]`,
`dim_borrower[nace_code]`, `dim_borrower[country]`, `Exposure EUR`,
`dim_borrower[bank_transition_rating]`.

**Visual 1, the cost path.** Line chart.

- X axis: `dim_year[year]`, 2026 to 2035
- Y axis: `Total Cost EUR`
- Legend: `dim_scenario[scenario_label]`, all three lines at once
- The branch slicer still applies, so the chart shows three scenarios on one
  branch. Add a small multiple on `dim_branch[branch_label]` if you want all six
  at once, which is worth it on this page.
- Data colours: the first three theme colours, which differ in lightness.
- On a fixture export all three lines are the same shape, because the fixture
  prices are flat. That is not a bug and it is a useful check that the chart is
  wired correctly. Real NGFS paths will diverge.

**Visual 2, the liquidity chart.** Clustered column chart.

- X axis: `fact_liquidity[window]`, sorted by `fact_liquidity[year]` then
  `fact_liquidity[quarter]`
- Y axis: `Cash-Out EUR`
- Legend: `fact_liquidity[kind]`, which is block, prepayment or settlement
- Filter to payment years 2027 and 2028, which is where the shock is.
- Add a line on the secondary axis for `Cash-Out % of Working Capital` if
  working capital is known, and hide the line when it is blank rather than
  drawing it at zero.

The story this chart tells: the 2026 obligation cannot be paid before 1 February
2027, so it lands as one block, and the 2027 obligation starts prepaying in the
same year. Two obligations in one year is the shock.

**Visual 3, why this rating.** Card with the `Why This Rating` measure, or a
textbox bound to it. Give it plenty of width, word wrap on, left aligned,
background `#F2F5F3`.

It prints something like: the borrower is shown as "Imports CBAM goods"; the
tier reason; the cost basis; counting tonnage, carbon price and free allocation
share; then the cost against EBITDA and the band; then the line saying the band
boundaries are the author's assumptions.

**Visual 4, what to ask this client.** Card with the `What To Ask This Client`
measure. One paragraph per active flag, each one the engine's own sentence with
this borrower's numbers already in it.

When CLAUDE.md Task 7's `config/questions.yaml` is written, the exporter gains a
question column on `fact_flags` and this measure reads that instead of the
reason. Nothing else on the page changes.

### Page 4: Summary

The one-pager for a risk committee. Design it to print to A4 landscape, so set
the page size to Letter or A4 under `Format > Canvas settings`.

Layout, top to bottom:

1. Banner, as on every page.
2. Title: "CBAM screening summary", then a subtitle line with `Generated At`
   and `Price Source`.
3. Card row: `Exposure EUR`, `Borrower Count`, `Total Cost EUR` for the default
   scenario and branch, `2027 Cash-Out`, `Exposure Carrying RATING_GAP`.
4. The scenario range: a table of `Best Case Cost EUR` and `Worst Case Cost EUR`
   with the two text measures beside them, one row, no breakdown.
5. Top twenty borrowers: table filtered to `Top 20 Rank by Ratio` less than or
   equal to 20, with rank, name, plain tier label, band, cost, ratio, exposure
   and flags.
6. Flag counts: bar chart of `Flag Count` by `fact_flags[flag]`, sorted
   descending, bars in amber.
7. Method note, a textbox, fixed text:

   > Costs are estimates from sector defaults and default emission values unless
   > a borrower's supplier data is marked verified. Materiality bands are the
   > author's assumptions in config/thresholds.yaml, not law and not supervisory
   > guidance. The proposal branch applies two Commission proposals that are not
   > law. Tier 4 costs are not yet computed. See docs/METHODOLOGY.md.

Export with `File > Export to PDF`.

---

## 6. Refreshing after the engine changes

```
python -m src.export_powerbi \
    --borrowers data/powerbi_fixture/borrowers_fixture.csv \
    --config-dir tests/fixtures \
    --prices data/powerbi_fixture/prices_fixture.yaml \
    --out data/powerbi_fixture/export
```

Then `Home > Refresh` in Desktop. The model reads whatever is in the folder, so
no model change is needed unless a column was added or renamed. If one was,
`tests/test_powerbi_project.py` fails and tells you which table drifted.

`python -m src.export_powerbi --help` lists the other arguments. The ones worth
knowing are `--data-label`, `--first-year` and `--last-year`, and the three
`--flag-*` arguments that move the reference point the flags are evaluated at.

---

## 7. The gates

Two inputs are missing, and neither can be filled by guessing. CLAUDE.md section
2 says so plainly: if a source cannot be fetched, write a TODO naming the exact
document needed and stop.

### Gate 1: carbon prices

**What is fixture:** every price in the export. `data/powerbi_fixture/prices_fixture.yaml`
holds a flat 100, 80 and 60 euro per tonne, labelled `fixture_not_a_forecast`. A
flat path is deliberate, because no flat path can be mistaken for a scenario.

**Why:** `config/scenarios.yaml` is gated. The NGFS Phase V carbon prices for the
EU are not published in any document that could be fetched. The Scenario
Explorer is behind a login, the Zenodo record's files are restricted, and the
published NGFS narratives give global or R5 numbers at milestone years only,
with nothing at all for Delayed Transition.

**What unlocks it, and where:**

1. Log in to the NGFS Scenario Explorer at
   `https://data.ece.iiasa.ac.at/ngfs/#/downloads` with a guest account. This is
   the owner's to authorise, not the repo's.
2. Download the variable `Price|Carbon` for the EU or Europe region, annual 2026
   to 2035, for Net Zero 2050, Delayed Transition and Current Policies.
3. The unit there is US dollars of 2010 per tonne. A conversion to euro and a
   restatement to a stated price year are both needed, and both need their own
   source recorded in `data/SOURCES.md`.
4. Check the region label against the model region mapping in the NGFS Technical
   Documentation V5.0, pages 166 and 201. Regional aggregates are emissions
   weighted and the native model regions differ between REMIND, MESSAGE and GCAM.
5. Fill `config/scenarios.yaml` in the schema its own comment block sets out,
   with `interpolated: true` on any year you had to interpolate.
6. Run the exporter **without** `--prices`. Nothing else changes.

### Gate 2: the real portfolio

**What is fixture:** the thirteen borrowers in
`data/powerbi_fixture/borrowers_fixture.csv`, and the sector mapping in
`tests/fixtures/nace_tiers.yaml` that the export runs against.

**Why:** `config/nace_tiers.yaml` is still the scaffold. CLAUDE.md Task 4 says
the owner writes that file by hand, and `config/nace_tiers.DRAFT.yaml` is the
schema-correct starting point. And `data/abn_sector_mix.csv`, which Task 8's
synthetic generator needs, has not been pasted in from the annual report.

**What unlocks it, and where:**

1. Write `config/nace_tiers.yaml` from `config/nace_tiers.DRAFT.yaml`.
2. Paste the corporate loans by industry table from the ABN AMRO Annual Report
   2025, around pages 235 to 236, into `data/abn_sector_mix.csv`.
3. Build `src/synth.py` (Task 8) and generate `data/synthetic_portfolio.csv`.
4. Run the exporter with `--borrowers data/synthetic_portfolio.csv`, no
   `--config-dir` override, and `--data-label SYNTHETIC`.

The banner then reads SYNTHETIC rather than FIXTURE, and every page picks that
up without an edit, because the label is data.

### Gate 3, smaller: Tier 4 costs

`src/portfolio.py` does not exist yet, and the Tier 4 cost formula needs the
upstream sector cost uplift that it will compute from the Tier 2 results. Until
Task 9 is done, Tier 4 rows carry a cost of zero and the `cost_basis`
`input_share_uplift_unavailable`. Show `cost_basis` wherever a zero can appear.

---

## 8. Who does what

**The repo provides:**

- the engine, its config and its tests
- `src/export_powerbi.py` and the star schema it writes
- the fixture portfolio and the fixture prices, both labelled in the files
  themselves
- the semantic model: tables, columns, types, relationships, the DataFolder
  parameter and all twenty-six measures with their descriptions
- the theme, the band ramp and the colour rules
- these page blueprints
- tests that keep the model and the exporter in step

**The owner does:**

- switches on the two preview features and opens the project
- sets DataFolder and refreshes
- imports the theme
- builds the visuals from section 5, which is the part that is a skill showcase
  and cannot be delegated to a text file
- arranges, sizes and polishes
- clears the gates in section 7 and re-runs the exporter

**Neither does:** invents a number. If a figure cannot be traced to a config
entry, an uploaded field or `meta.csv`, it does not go on a page.

---

## 9. What was checked, and what was not

### Checked here

`tests/test_powerbi_project.py` runs with the rest of the suite and asserts:

- every json file parses, and carries the keys its published schema marks
  required
- the `.pbip` points at a folder that exists, and the report points back at the
  model by relative path
- `definition.pbism` declares a version of 4.0 or above, which is what TMDL needs
- the two items do not share a `logicalId`
- TMDL indents with tabs
- every `ref table` has a file and every file is referenced
- every table file declares the table its file name promises
- every csv-backed table has an import partition with a closed `let` and `in`,
  balanced brackets, and reads its path from the DataFolder parameter
- no partition hard-codes a path
- every relationship points at a column that exists
- every `sortByColumn` target is a column in the same table
- **the model's columns equal the exporter's columns**, per table, in order,
  each bound to its own source column and each typed in the M query
- every measure carries a description, every measure reference resolves, and
  every table-qualified column reference resolves
- no materiality band boundary appears as a literal in any DAX expression
- the theme parses, every colour is a six-digit hex, the first four data colours
  differ in lightness, and the band ramp runs light to dark

### Not checked, and cannot be

**Whether Power BI Desktop opens the project.** Only Desktop can answer that,
and it is a graphical application. Everything below is a residual risk that this
delivery is honest about rather than hiding.

Confirmed against Microsoft's published JSON schemas and the official sample
PBIP, so low risk:

- the `.pbip` shape, including that `report` is the only artifact type and that
  it is required, which is why an empty report folder exists at all
- `definition.pbism`, `.platform`, `definition.pbir`
- TMDL file layout, the `///` description syntax, tab indentation, `ref table`,
  the `partition <name> = m` with `mode:` and `source =` form, the `expression
  <name> = "..." meta [IsParameterQuery=true, Type="Text"]` parameter form, and
  that `lineageTag` is optional
- the PBIR `report.json`, `version.json`, `pages.json` and `page.json` required
  keys, and the `displayOption` values

Not confirmed by any Microsoft example, so a residual risk:

- `sortByColumn` in TMDL. It is a standard TOM property and TMDL's rule is that
  every TOM property is exposed in camelCase, but no published example shows it.
  If Desktop rejects it, delete those four lines and set the sort column in the
  Desktop UI instead.
- `displayFolder` on measures. Same reasoning, same fix.
- `compatibilityLevel: 1601` in `database.tmdl`. The official sample uses it.
  The Learn prose example uses 1567. If 1601 is rejected, try 1567.
- the `version` value `1.0.0` in the report's `version.json`. The schema allows
  it and states no default, and no published example gives a value.
- `"themeCollection": {}` in `report.json`. The key is required but the schema
  marks none of its own sub-properties required, so an empty object is valid
  against the schema. Whether Desktop is happy with it is untested.

Deliberately not attempted:

- **visual JSON.** The PBIR container schemas are published, but the enumeration
  of `visualType` strings is not, no worked minimal example of any specific
  visual is published, and the `objects` and `query` payload semantics inside a
  visual are not documented. Microsoft's own advice is to build the visual in
  Desktop and read the emitted file. A hand-written `visual.json` would be a
  guess presented as a deliverable, so the canvas is empty and the visuals are
  specified in prose in section 5 instead.

### Sources consulted for the file formats

Microsoft Learn:

- `learn.microsoft.com/power-bi/developer/projects/projects-overview`
- `learn.microsoft.com/power-bi/developer/projects/projects-dataset`
- `learn.microsoft.com/power-bi/developer/projects/projects-report`
- `learn.microsoft.com/analysis-services/tmdl/tmdl-overview`
- `learn.microsoft.com/analysis-services/tmdl/tmdl-how-to`
- `learn.microsoft.com/analysis-services/tom/lineage-tags-for-power-bi-semantic-models`
- `learn.microsoft.com/fabric/cicd/git-integration/source-code-format`
- `learn.microsoft.com/rest/api/fabric/articles/item-management/definitions/semantic-model-definition`
- `learn.microsoft.com/power-bi/transform-model/desktop-tmdl-view`
- `learn.microsoft.com/power-bi/create-reports/desktop-report-themes`
- `learn.microsoft.com/power-bi/create-reports/report-themes-create-custom`

Published JSON schemas, read directly:

- `github.com/microsoft/json-schemas/tree/main/fabric`
- `fabric/pbip/pbipProperties/1.0.0/schema.json`
- `fabric/item/semanticModel/definitionProperties/1.0.0/schema.json`
- `fabric/item/report/definition/report/3.3.0/schema.json`
- `fabric/item/report/definition/page/2.1.0/schema.json`
- `fabric/item/report/definition/pagesMetadata/1.1.0/schema.json`
- `fabric/item/report/definition/versionMetadata/1.0.0/schema.json`
- `fabric/item/report/definition/visualContainer/2.9.0/schema.json`
- `fabric/item/report/definition/visualConfiguration/2.3.0/schema-embedded.json`

Official sample project, read for the TMDL house style:

- `github.com/microsoft/Analysis-Services`, `pbidevmode/fabricps-pbip/SamplePBIP/`

Theme schema:

- `github.com/microsoft/powerbi-desktop-samples`, `Report Theme JSON Schema/`

All consulted on 2026-09-14.

### Two things worth knowing that bit other people

- **Windows path length.** A PBIP save can fail at the 260 character limit if the
  repo sits deep in a folder tree and table names are long. Keep the checkout
  near the drive root.
- **PBIR is becoming mandatory.** Microsoft states that when the PBIR report
  format reaches general availability it will be the only supported report
  format and conversion will be compulsory. The empty report folder here is
  already PBIR, so nothing in this delivery has to be converted later.
