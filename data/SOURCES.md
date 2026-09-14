# Sources

Every number in config/ traces to an entry here. Each entry records the title,
the URL, the date it was retrieved and exactly what was taken from it.

Status vocabulary: **adopted** means in force or formally adopted law.
**proposal** means a Commission proposal that is not law. **assumption** means
the author's own choice, not taken from any source. **scenario** means a
projection, which is neither law nor an assumption of this tool.

All retrievals below are dated **2026-09-14**.

## A note on EUR-Lex access

On 2026-09-14 `eur-lex.europa.eu` refused every automated request, returning
HTTP 202 with an empty body on HTML, PDF and ELI endpoints. `publications.europa.eu`
CELLAR returned HTTP 400, and `taxation-customs.ec.europa.eu` returned HTTP 403.
Values were therefore read from byte-identical copies of the official PDFs hosted
elsewhere, named in each entry. The canonical EUR-Lex address is given alongside
and should be used to re-verify from a browser. **Anyone relying on these numbers
should re-check them against the Official Journal text.**

---

## 1. Regulation (EU) 2023/956, the CBAM Regulation

- **Title:** Regulation (EU) 2023/956 establishing a carbon border adjustment
  mechanism. Consolidated text `02023R0956`, EN version dated 20.10.2025, revision 001.001.
- **Canonical URL:** https://eur-lex.europa.eu/eli/reg/2023/956/oj/eng
  (consolidated: CELEX `02023R0956-20251020`)
- **Copy actually read:** https://cdn.climatepolicyradar.org/navigator/EUR/2023/regulation-eu-2023-956-establishing-a-carbon-border-adjustment-mechanism-amended-by-regulation-eu-2025-2083-cbam_4cc762319b9a9fd0e05ea0a09973768c.pdf
- **Retrieved:** 2026-09-14
- **Status:** adopted

**Taken from it:**

- Annex I, the CN codes for each sector. These populate `goods.*.cn_codes` in
  `config/cbam_rules.yaml`, including the exclusions under Iron and steel
  (`7202 2`, `7202 30 00`, `7202 50 00`, `7202 70 00`, `7202 80 00`,
  `7202 91 00`, `7202 92 00`, `7202 93 00`, `7202 99`, `7204`) and under
  Fertilisers (`3105 60 00`).
- Annex I greenhouse gas column for Cement, Electricity, Iron and steel,
  Aluminium and Chemicals. Aluminium is the only sector covering perfluorocarbons
  as well as carbon dioxide.
- Annex II, the list of goods for which only direct emissions count. This drives
  `goods.*.emissions_covered`. **Electricity was moved into Annex II by
  Regulation (EU) 2025/2083**, so under the law in force only Cement and
  Fertilisers carry indirect emissions.
- Annex III point 1, the origins outside scope: Iceland, Liechtenstein, Norway
  and Switzerland, plus the territories Büsingen, Heligoland, Livigno, Ceuta and
  Melilla. This populates `exempt_origins`.
- Annex III point 2, the electricity-specific exemption list, confirmed to be an
  **empty placeholder** in the current consolidated text.
- Confirmed **absent** from Annex III: Campione d'Italia and the Italian waters
  of Lake Lugano. Recorded in `exempt_origins.verified_absent` because they
  appear in other EU customs and VAT instruments and their absence here is easy
  to get wrong.
- Article 31, which contains **no schedule** and only cross-refers to
  Article 10a of Directive 2003/87/EC. Applies from 1 January 2026.

**Could not verify:** the greenhouse gas column for **Fertilisers**, and the tail
of the `3105` description. The Fertilisers table is clipped at the right page
margin in the only fetchable copy, so those cells are absent from the text layer.
See the TODO in `config/cbam_rules.yaml` under `goods.fertiliser`.

---

## 2. Regulation (EU) 2025/2083, the CBAM Simplification Regulation

- **Title:** Regulation (EU) 2025/2083 of the European Parliament and of the
  Council of 8 October 2025 amending Regulation (EU) 2023/956 as regards
  simplifying and strengthening the carbon border adjustment mechanism.
- **Official Journal:** OJ L, 2025/2083, 17.10.2025
- **Canonical URL:** http://data.europa.eu/eli/reg/2025/2083/oj
- **Procedure:** 2025/0039(COD). Parliament vote 10 September 2025, Council
  29 September 2025.
- **Read from:** the consolidated text above (amendment marker M1), plus
  footnote 7 of COM(2025) 989 final for the citation.
- **Retrieved:** 2026-09-14
- **Status:** adopted

**The regulation number in the project brief was correct and is confirmed.**

**Taken from it, all four claims confirmed:**

| Claim | Confirmed | Article |
|---|---|---|
| 50 t/year mass threshold, excluding electricity and hydrogen | Yes | Art. 2a and Annex VII point 1; Art. 2a(4) for the exclusions |
| Certificate sales start 1 February 2027 | Yes | Art. 20(1) |
| 2026 goods priced at the quarterly average of 2026 ETS auctions | Yes | Art. 21(1a) |
| Declaration and surrender by 30 September of the following year | Yes | Art. 6(1) and Art. 22(1) |

Two details found that the brief did not mention, both recorded in
`config/cbam_rules.yaml`:

- **Article 2a(2), retroactivity.** Once the 50 t threshold is exceeded during a
  year, the importer is liable for all emissions embedded in all goods imported
  in that whole calendar year, back to 1 January, not only from the point of
  crossing. Recorded as `mass_threshold.retroactive_on_breach`.
- **Article 2a(3), annual review.** The Commission reviews the threshold by
  30 April each year against a target of 1 percent of embedded emissions, and may
  amend it by delegated act where the recomputed value differs by more than
  15 tonnes. So 50 tonnes is not a permanent constant. Recorded as
  `mass_threshold.review_note`.
- **Article 21(1a) precision.** The 2026 price is the quarterly average for the
  quarter in which the goods were imported, not a single annual figure. The
  normal rule in Article 21(1) is a **weekly** average, not weekly and not
  monthly. Both recorded in `certificate_price_method`.
- **Article 22(2).** From 1 January 2027 a declarant must hold certificates
  covering at least 50 percent of embedded emissions at the end of each quarter.
  This is the basis of the liquidity model in `config/thresholds.yaml`.

---

## 3. Directive 2003/87/EC, the ETS Directive, free-allocation phase-out

- **Title:** Directive 2003/87/EC establishing a system for greenhouse gas
  emission allowance trading. Consolidated text `02003L0087`, EN version dated
  01.03.2024, revision 016.001. Article 10a(1a) was inserted by Directive (EU) 2023/959.
- **Canonical URL:** https://eur-lex.europa.eu/eli/dir/2003/87/2024-03-01
  (CELEX `02003L0087-20240301`)
- **Copy actually read:** https://faolex.fao.org/docs/pdf/eur40305.pdf
- **Retrieved:** 2026-09-14
- **Status:** adopted

**Taken from it:** the year-by-year CBAM factor in Article 10a(1a), third
subparagraph, quoted verbatim into `free_allocation.adopted.quoted_text`.

**Important correction to the project brief.** The brief described the CBAM
factor as the phase-out percentage rising from 2.5 percent in 2026 to
100 percent in 2034. **In the Directive the CBAM factor is the share of free
allocation that is RETAINED**, so it runs 97.5 percent in 2026 down to nothing
from 2034. The brief's numbers are right as *phase-out* percentages; they are the
complement of the CBAM factor. Getting this backwards would invert every cost the
tool produces, so `config/cbam_rules.yaml` records both numbers on every year and
`tests/test_cbam_rules.py::test_cbam_factor_and_free_allocation_agree` guards it.

| Year | CBAM factor, i.e. free allocation retained |
|---|---|
| to end 2025 | 100 % |
| 2026 | 97.5 % |
| 2027 | 95 % |
| 2028 | 90 % |
| 2029 | 77.5 % |
| 2030 | 51.5 % |
| 2031 | 39 % |
| 2032 | 26.5 % |
| 2033 | 14 % |
| 2034 onward | no CBAM factor applies |

Note the Directive says "no CBAM factor shall apply" from 2034 rather than
"0 percent". Recorded as 0.0 with that wording in the source field.

---

## 4. COM(2025) 989 final, downstream extension proposal

- **Title:** Proposal for a Regulation amending Regulation (EU) 2023/956 as
  regards the extension of its scope to downstream goods and anti-circumvention
  measures. COM(2025) 989 final, 2025/0419 (COD), Brussels, 17 December 2025.
- **Canonical URL:** EUR-Lex CELEX `52025PC0989`
- **Copies actually read:**
  - Main act: https://www.regeringen.se/contentassets/7378dc9378ee4909b164eb7209601f7d/koms-forslag-com2025989-final-proposal-for-a-regulation-of-the-european-parliament-and-of-the-council-amending-regulation-eu-2023956-as-regards-the-extension-of-its-scope-to-downstream-goods-and-anti-circumvention-measures.pdf
  - Annexes 1 to 3: Council register ST-16973-2025-ADD-1,
    https://data.consilium.europa.eu/doc/document/ST-16973-2025-ADD-1/en/pdf
- **Retrieved:** 2026-09-14
- **Status:** proposal. Not law.

**Taken from it:**

- Start date **1 January 2028**, from Article 2 and recital 53.
- Scope: selected steel and aluminium intensive downstream products, chosen on
  trade intensity and a carbon cost-push indicator with an emissions floor. This
  is option 2 of three assessed, the balanced extension. About **180 additional
  CN codes**, with roughly half the newly captured importers being SMEs.
- The memorandum states explicitly that downstream products of **cement,
  fertilisers and hydrogen are not included**, and downstream products of
  electricity were not considered.
- **Scope is defined purely by CN code. The proposal uses no NACE codes at all.**
  Recorded as `downstream_extension.scope_defined_by: cn_code`, with a note that
  the `downstream_nace` list in `nace_tiers` is the author's own mapping.
- Eleven new Iron and steel CN codes, transcribed in full into
  `downstream_extension.iron_and_steel_additions`.
- The new Annex VIII precursor list: `ex 7204` ferrous scrap and `ex 7602`
  aluminium scrap, both except post-consumer scrap.

**Not transcribed:** the full CN code list of the new "Combined metal products"
table, which runs about 15 PDF pages. See the TODO in `config/cbam_rules.yaml`
under `downstream_extension.combined_metal_products_note`.

**Not used:** press reporting of 277 tariff lines. That figure is the European
Parliament ENVI committee position, not the Commission proposal, and was not
verified against a primary source.

---

## 5. COM(2026) 616 final, slower free-allocation phase-out proposal

- **Title:** Proposal for a Directive amending Directive 2003/87/EC and Decision
  (EU) 2015/1814 as regards driving competitiveness and cost-effective
  decarbonisation. COM(2026) 616 final, 2026/0212 (COD), Brussels, 17 July 2026.
- **Canonical URL:** https://climate.ec.europa.eu/document/download/c0b4ca8e-0e12-4b4e-9976-98c0b4224410_en
  which redirects to EUR-Lex `COM:2026:616:FIN`
- **Copy actually read:** https://www.ener-efficiency.eu/wp-content/uploads/2026/07/ETS-reform.pdf
  (cover page matches COM(2026) 616 final exactly)
- **Press release:** IP/26/1596, https://ec.europa.eu/commission/presscorner/detail/en/ip_26_1596
  (returned a shell page only, not readable; the COM document text is the
  stronger source and was used instead)
- **Retrieved:** 2026-09-14
- **Status:** proposal. Not law.

**The year-by-year schedule IS published**, in Article 1(15)(b)(i) of the
proposal, replacing the second subparagraph of Article 10a(1a). It is quoted
verbatim into `free_allocation.proposal_2038.quoted_text`. Nothing was
interpolated.

| Year | Proposed CBAM factor | Adopted law |
|---|---|---|
| 2026 | 97.5 % | 97.5 % |
| 2027 | 95 % | 95 % |
| 2028 | 91.5 % | 90 % |
| 2029 | 81 % | 77.5 % |
| 2030 | 59 % | 51.5 % |
| 2031 | 48 % | 39 % |
| 2032 | 37.5 % | 26.5 % |
| 2033 | 27 % | 14 % |
| 2034 to 2037 | 15 % | 0 % |
| 2038 onward | 0 % | not applicable |

Decimal separators are inconsistent in the original, "91.5 %" with a point next
to "97,5 %" with a comma. Reproduced as printed in the quoted text.

Also recorded but **not modelled**: a separate ramp for goods added to CBAM
Annex I later, at 97.9 percent in year one and 95.8 percent in year two before
rejoining the main schedule. The tool has no concept of newly added goods.

---

## 6. Commission default values workbook, definitive period

- **Title:** CBAM default values, definitive period. Annex I to Commission
  Implementing Regulation (EU) 2025/2621, as corrected by Commission
  Implementing Regulation (EU) 2026/1740. Applies from 1 January 2026.
- **Local file:** `data/raw/CBAM_default_values_definitive_v20260204.xlsx`,
  copied read-only from an existing local cache. `data/raw/` is gitignored.
- **Retrieved:** cached locally and verified in September 2026
- **Status:** adopted

**Taken from it:** the `_Other Countries and Territorie` sheet, which carries the
fallback values that apply when origin-specific values are not used. All
**260 CN code rows** were extracted by `tools/extract_default_values.py` into the
`by_cn` block of `config/emission_defaults.yaml`, and five of them were selected
as sector representatives for `good_groups`.

| Group | Representative CN | Direct | Indirect | Total |
|---|---|---|---|---|
| cement | 2523 29 00, grey Portland cement | 1.360 | 0.080 | 1.440 |
| fertiliser | 3102 10 19, urea over 45 % N | 2.600 | 0.120 | 2.720 |
| steel | 7208, hot-rolled flat | 4.049 | none | 4.049 |
| aluminium | 7601, unwrought | 2.203 | none | 2.203 |
| hydrogen | 2804 10 00 | 17.740 | none | 17.740 |

Sector ranges are also recorded, because a single representative hides real
spread. Iron and steel spans 200 CN rows from 0.686 to 7.100 tCO2e per tonne.

**Two things worth flagging about this file:**

1. **The filename understates the version.** The Version History sheet lists
   version 1 dated 2026-02-04 based on IR (EU) 2025/2621, and **version 2 dated
   2026-08-06 based on Annexes I and II to IR (EU) 2026/1740 adopted 20 July
   2026**. The workbook content is version 2. The cached filename carries the
   version 1 date. Recorded in `meta.workbook_version`.
2. **The Overview disclaimer contradicts the version history.** The disclaimer,
   written for version 1, says the file "does not contain the default values for
   indirect emissions and for electricity as a CBAM good contained in Annexes II
   and III to Commission Implementing Regulation (EU) 2025/2621". The sheet in
   fact **does** carry indirect values for cement and fertilisers, consistent
   with the version 2 history entry. The indirect values are recorded as found,
   with the conflict noted in `meta.caveat`.

**Electricity is genuinely absent** from this workbook, confirmed by the
disclaimer. See the TODO below.

---

## 7. NGFS Phase V carbon prices: NOT OBTAINED

**Task 3 is gated. No price is recorded anywhere in this repository.**

Searched on 2026-09-14, in this order:

1. **NGFS Phase 5 Scenario Explorer**, https://data.ene.iiasa.ac.at/ngfs/.
   Login-walled; redirects to a login page. Anonymous API endpoints returned 403
   and 404. The Zenodo record, DOI 10.5281/zenodo.13989530, is publicly listed
   but its files are restricted and it redirects back to the Explorer.
2. **NGFS published Phase V documentation.** Four documents read in full:
   - NGFS Climate Scenarios for central banks and supervisors, Phase V,
     November 2024, https://www.ngfs.net/system/files/2025-01/NGFS%20Climate%20Scenarios%20for%20central%20banks%20and%20supervisors%20-%20Phase%20V.pdf
   - NGFS long-term climate scenarios Phase V, high-level overview,
     https://www.ngfs.net/system/files/import/ngfs/media/2024/11/05/ngfs_scenarios_high-level_overview.pdf
   - NGFS Climate Scenarios Technical Documentation V5.0, 245 pages,
     https://www.ngfs.net/system/files/2025-01/NGFS%20Climate%20Scenarios%20Technical%20Documentation.pdf
   - NGFS long-term scenarios, narratives and key findings, 7 November 2025,
     https://www.ngfs.net/system/files/2025-11/NGFS%20scenarios%20narratives%20and%20key%20findings_0.pdf

   Every carbon price in these appears either as a chart with no data labels, or
   as prose at a **global** or **R5 "OECD and EU"** aggregate, at milestone years
   only. Nothing at EU27 or Europe granularity. Nothing annual.
3. **EU-institution publications citing Phase V with numbers.** None found. ECB
   Occasional Paper 336 uses Phase III. The Fit-for-55 one-off climate scenario
   analysis uses Phase IV. The 2025 EU-wide stress test uses the NGFS short-term
   NDC path.

**What is published, recorded as evidence only and NOT used as a price:**

| Scenario | Region | Value | Source |
|---|---|---|---|
| Net Zero 2050 | global, weighted | rises from 98 to 294 USD2010/tCO2, reaching about 294 by 2035 | Phase V main report, p. 21 |
| Net Zero 2050 | R5 OECD and EU | at least 150 USD/tCO2 by 2030; 450 USD/tCO2 by 2050 | narratives note, p. 24 |
| Current Policies | global | up to 10 USD2010/tCO2 by 2030 | narratives note, p. 34 |
| Delayed Transition | any | **no published number found, any region, any year** | none |

**The ordering property could not be validated.** Net Zero above Current Policies
holds at the global level from the published numbers. Delayed Transition has no
published number at all. Note also that Delayed Transition introduces carbon
pricing *in* 2030, so at 2030 exactly its price may not yet exceed Current
Policies. The strict ordering must not be encoded as validated.

**TODO, the exact document needed:** NGFS Phase V variable `Price|Carbon`, EU or
Europe region, annual 2026 to 2035, for Net Zero 2050, Delayed Transition and
Current Policies, from the NGFS Scenario Explorer bulk download at
https://data.ece.iiasa.ac.at/ngfs/#/downloads. This requires a guest login, which
is the owner's to authorise. The unit there is USD2010 per tCO2, so a currency
conversion and a price-year restatement are also needed, each with its own
recorded source. Regional aggregates in the Explorer are emissions-weighted and
native model regions differ between REMIND, MESSAGE and GCAM, so the region label
must be checked against the model region mapping in the Technical Documentation
V5.0, pages 166 and 201.

---

## Sources not yet needed

These are listed in CLAUDE.md section 3 but belong to later tasks.

- **8. Eurostat Comext**, NL extra-EU imports by CN8, 2023 to 2025, tonnes. Needed
  for the sector import intensities in `nace_tiers.yaml`. Owner's input.
- **9. CBS StatLine input-output tables.** Needed for the pass-through shares.
  Owner's input.
- **10. ABN AMRO Annual Report 2025**, corporate loans by industry. Needed for
  Task 8, the synthetic portfolio. Owner pastes `data/abn_sector_mix.csv`.
- **11. ECB Guide on climate-related and environmental risks, 2020.** Needed for
  Task 11, the methodology write-up.

---

## Open TODOs

| # | Where | What is needed | Blocks |
|---|---|---|---|
| 1 | `scenarios.yaml` | NGFS Phase V `Price|Carbon`, EU, annual 2026 to 2035, three scenarios. Explorer login required. | All cost output. Task 3 test is a strict xfail gate. |
| 2 | `emission_defaults.yaml` | Annex III to IR (EU) 2025/2621, default values for electricity as a CBAM good, in tCO2e per MWh per origin. | Any electricity cost. |
| 3 | `cbam_rules.yaml` `goods.fertiliser` | Annex I greenhouse gas column for Fertilisers, from the OJ PDF, OJ L 130, 16.5.2023, p. 52. | Nothing numeric. Gas list only. |
| 4 | `cbam_rules.yaml` `downstream_extension` | Full CN list of the Combined metal products table, from ST-16973-2025-ADD-1. | Nothing today. Tier 3 runs on NACE. |
| 5 | `nace_tiers.yaml` | Owner writes the file. Draft at `config/nace_tiers.DRAFT.yaml`. Every intensity and pass-through share is `TODO-owner`. | Tier 1 and Tier 4 cost magnitudes. |
