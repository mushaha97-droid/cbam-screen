/* CBAM Borrower Screening Panel, web dashboard.
 *
 * What this file is allowed to do: read the csv star schema the Python engine
 * exported, add up, rank, filter and draw. That is all.
 *
 * What it must never do: compute a CBAM number. No cost formula, no band
 * boundary, no threshold, no price and no flag rule appears anywhere below. If
 * a figure on screen is not a value from the export or a sum, a count, a
 * minimum, a maximum or a rank over those values, it is a bug. The engine in
 * src/ is the single source of truth and it stays that way.
 *
 * No dependencies, no build step, no network call except the fonts. The files
 * a user drags in are read in the browser and never leave the machine.
 */

/* eslint-env browser */
(function () {
  "use strict";

  // --------------------------------------------------------------- tables

  var REQUIRED_TABLES = [
    "dim_borrower",
    "dim_scenario",
    "dim_branch",
    "dim_year",
    "fact_cost",
    "fact_liquidity",
    "fact_flags",
    "meta"
  ];

  // Written by the same exporter, but a page still works without them: the
  // formula box and the question box say what is missing instead of guessing.
  var OPTIONAL_TABLES = ["fact_cost_line", "questions"];
  var ALL_TABLES = REQUIRED_TABLES.concat(OPTIONAL_TABLES);

  // ------------------------------------------------------------ csv reader

  /* A small reader for the files this repo writes: comma separated, double
   * quotes around any field containing a comma, quote or newline, a doubled
   * quote inside a quoted field. That is what Python's csv module emits with
   * the default dialect, which is what src/export_powerbi.py uses. */
  function parseCsv(text) {
    var rows = [];
    var row = [];
    var field = "";
    var quoted = false;
    var index = 0;
    if (text.charCodeAt(0) === 0xfeff) {
      text = text.slice(1);
    }
    while (index < text.length) {
      var character = text[index];
      if (quoted) {
        if (character === '"') {
          if (text[index + 1] === '"') {
            field += '"';
            index += 2;
            continue;
          }
          quoted = false;
          index += 1;
          continue;
        }
        field += character;
        index += 1;
        continue;
      }
      if (character === '"') {
        quoted = true;
        index += 1;
        continue;
      }
      if (character === ",") {
        row.push(field);
        field = "";
        index += 1;
        continue;
      }
      if (character === "\r") {
        index += 1;
        continue;
      }
      if (character === "\n") {
        row.push(field);
        rows.push(row);
        row = [];
        field = "";
        index += 1;
        continue;
      }
      field += character;
      index += 1;
    }
    if (field !== "" || row.length) {
      row.push(field);
      rows.push(row);
    }
    if (!rows.length) {
      return [];
    }
    var header = rows[0];
    var out = [];
    for (var r = 1; r < rows.length; r += 1) {
      if (rows[r].length === 1 && rows[r][0] === "") {
        continue;
      }
      var record = {};
      for (var c = 0; c < header.length; c += 1) {
        record[header[c]] = rows[r][c] === undefined ? "" : rows[r][c];
      }
      out.push(record);
    }
    return out;
  }

  function toCsv(header, rows) {
    function cell(value) {
      var text = value === null || value === undefined ? "" : String(value);
      if (/[",\n\r]/.test(text)) {
        return '"' + text.replace(/"/g, '""') + '"';
      }
      return text;
    }
    var lines = [header.map(cell).join(",")];
    rows.forEach(function (row) {
      lines.push(header.map(function (key) { return cell(row[key]); }).join(","));
    });
    return lines.join("\n") + "\n";
  }

  // --------------------------------------------------------------- helpers

  function num(value) {
    if (value === undefined || value === null || String(value).trim() === "") {
      return null;
    }
    var parsed = Number(value);
    return isNaN(parsed) ? null : parsed;
  }

  function isTrue(value) { return String(value) === "true"; }

  var EUR = new Intl.NumberFormat("en-GB", { maximumFractionDigits: 0 });
  var EUR2 = new Intl.NumberFormat("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  var PLAIN = new Intl.NumberFormat("en-GB", { maximumFractionDigits: 3 });

  function eur(value) {
    if (value === null || value === undefined) { return "not known"; }
    return "EUR " + EUR.format(value);
  }

  function eur2(value) {
    if (value === null || value === undefined) { return "not known"; }
    return "EUR " + EUR2.format(value);
  }

  function pct(value, digits) {
    if (value === null || value === undefined) { return "not known"; }
    return (value * 100).toFixed(digits === undefined ? 2 : digits) + "%";
  }

  function plain(value) {
    if (value === null || value === undefined) { return ""; }
    return PLAIN.format(value);
  }

  function escapeHtml(value) {
    return String(value === null || value === undefined ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function sum(rows, getter) {
    var total = 0;
    for (var i = 0; i < rows.length; i += 1) {
      var value = getter(rows[i]);
      if (value !== null && value !== undefined) { total += value; }
    }
    return total;
  }

  function byId(id) { return document.getElementById(id); }

  function setHtml(id, html) {
    var node = byId(id);
    if (node) { node.innerHTML = html; }
  }

  // ----------------------------------------------------------------- state

  var data = null;   // the loaded dataset and its indexes
  var ui = {
    view: "portfolio",
    scenario: null,
    branch: null,
    year: null,
    borrowerId: null,
    search: "",
    tier: "",
    band: "",
    flag: "",
    sortKey: "exposure_eur",
    sortDir: -1
  };

  // -------------------------------------------------------------- indexing

  function buildDataset(tables, sourceNote) {
    var meta = {};
    (tables.meta || []).forEach(function (row) { meta[row.key] = row.value; });

    var borrowers = tables.dim_borrower || [];
    var borrowerById = {};
    borrowers.forEach(function (row) { borrowerById[row.borrower_id] = row; });

    // fact_cost, keyed for the selectors, and kept per borrower for the chart.
    var costByKey = {};
    var costByBorrower = {};
    var tierOrder = {};
    (tables.fact_cost || []).forEach(function (row) {
      costByKey[row.borrower_id + "|" + row.year + "|" + row.scenario + "|" + row.branch] = row;
      if (!costByBorrower[row.borrower_id]) { costByBorrower[row.borrower_id] = []; }
      costByBorrower[row.borrower_id].push(row);
      tierOrder[row.tier_label_plain] = num(row.tier);
    });

    var lineByKey = {};
    (tables.fact_cost_line || []).forEach(function (row) {
      var key = row.borrower_id + "|" + row.year + "|" + row.scenario + "|" + row.branch;
      if (!lineByKey[key]) { lineByKey[key] = []; }
      lineByKey[key].push(row);
    });

    var liquidityByBorrower = {};
    (tables.fact_liquidity || []).forEach(function (row) {
      if (!liquidityByBorrower[row.borrower_id]) { liquidityByBorrower[row.borrower_id] = []; }
      liquidityByBorrower[row.borrower_id].push(row);
    });

    var flagsByBorrower = {};
    var flagCodes = {};
    (tables.fact_flags || []).forEach(function (row) {
      flagCodes[row.flag] = true;
      if (!flagsByBorrower[row.borrower_id]) { flagsByBorrower[row.borrower_id] = []; }
      if (isTrue(row.active)) { flagsByBorrower[row.borrower_id].push(row); }
    });

    var questionsByFlag = {};
    (tables.questions || []).forEach(function (row) {
      if (!questionsByFlag[row.flag]) { questionsByFlag[row.flag] = []; }
      questionsByFlag[row.flag].push(row);
    });

    // Bands in the order thresholds.yaml lists them, carried by band_sort.
    var bandSort = {};
    var bandLabel = {};
    (tables.fact_cost || []).forEach(function (row) {
      bandSort[row.band] = num(row.band_sort);
      bandLabel[row.band] = row.band_label;
    });
    var bands = Object.keys(bandSort).sort(function (a, b) { return bandSort[a] - bandSort[b]; });

    var tiers = Object.keys(tierOrder).sort(function (a, b) { return tierOrder[a] - tierOrder[b]; });

    var years = (tables.dim_year || [])
      .filter(function (row) { return isTrue(row.is_obligation_year); })
      .map(function (row) { return row.year; });

    var scenarios = (tables.dim_scenario || []).slice().sort(function (a, b) {
      return num(a.sort_order) - num(b.sort_order);
    });
    var branches = (tables.dim_branch || []).slice().sort(function (a, b) {
      return num(a.sort_order) - num(b.sort_order);
    });

    return {
      tables: tables,
      meta: meta,
      sourceNote: sourceNote,
      borrowers: borrowers,
      borrowerById: borrowerById,
      costByKey: costByKey,
      costByBorrower: costByBorrower,
      lineByKey: lineByKey,
      liquidityByBorrower: liquidityByBorrower,
      flagsByBorrower: flagsByBorrower,
      flagCodes: Object.keys(flagCodes).sort(),
      questionsByFlag: questionsByFlag,
      bands: bands,
      bandLabel: bandLabel,
      tiers: tiers,
      years: years,
      scenarios: scenarios,
      branches: branches
    };
  }

  function costFor(borrowerId) {
    return data.costByKey[borrowerId + "|" + ui.year + "|" + ui.scenario + "|" + ui.branch] || null;
  }

  function linesFor(borrowerId) {
    return data.lineByKey[borrowerId + "|" + ui.year + "|" + ui.scenario + "|" + ui.branch] || [];
  }

  function shockYear() {
    return data.meta.liquidity_shock_year || "";
  }

  function shockCashOut(borrowerId) {
    var rows = data.liquidityByBorrower[borrowerId] || [];
    var year = shockYear();
    var wanted = rows.filter(function (row) {
      return row.scenario === ui.scenario && row.branch === ui.branch && row.year === year;
    });
    if (!wanted.length) { return 0; }
    return sum(wanted, function (row) { return num(row.cash_out_eur); });
  }

  // ---------------------------------------------------------------- banner

  /* meta.csv's own warning already opens with the data label, so the label is
   * not printed twice wherever the two are shown together. */
  function dataWarning() {
    var label = data.meta.data_label || "UNLABELLED";
    return (data.meta.fixture_warning || "Not any bank's actual exposure.")
      .replace(new RegExp("^" + label + "\\s+DATA\\.\\s*", "i"), "");
  }

  function renderBanner() {
    var label = data.meta.data_label || "UNLABELLED";
    var node = byId("data-banner");
    var parts = [];
    parts.push("<strong>" + escapeHtml(label) + " DATA.</strong> ");
    parts.push(escapeHtml(dataWarning()) + " ");
    parts.push("Carbon prices: " + escapeHtml(data.meta.price_source_label || "unknown source") + ". ");
    parts.push("Materiality bands are the author's assumptions in config/thresholds.yaml, not law.");
    node.innerHTML = parts.join("");
    node.className = "banner" + (label === "UPLOADED" ? " is-uploaded" : "");
    document.title = label + " data. CBAM Borrower Screening Panel";
  }

  function renderFooter() {
    var repo = "https://github.com/mushaha97-droid/mushaha97-droid.github.io/tree/main/cbam-screen";
    setHtml("footer", [
      "<p><strong>" + escapeHtml(data.meta.data_label || "UNLABELLED") + "</strong>",
      " data, exported " + escapeHtml(data.meta.generated_at || "at an unrecorded time"),
      " from engine " + escapeHtml(data.meta.engine_version || "version unknown"),
      " commit " + escapeHtml(data.meta.engine_commit || "unknown") + ".",
      " Carbon prices: " + escapeHtml(data.meta.price_source || "unknown") + ".",
      " Borrower list: " + escapeHtml(data.meta.borrower_source || "unknown") + ".",
      " Loaded from: " + escapeHtml(data.sourceNote) + ".</p>",
      "<p>Every number on this page traces to the engine export. The formulas live in the Python engine, ",
      "not in this page, which only adds up and ranks what the engine wrote. ",
      '<a href="' + repo + '" rel="noopener">Source, config and tests on GitHub</a>.</p>'
    ].join(""));
  }

  // ------------------------------------------------------------- selectors

  function fillSelectors() {
    var scenarioSelect = byId("sel-scenario");
    scenarioSelect.innerHTML = data.scenarios.map(function (row) {
      return '<option value="' + escapeHtml(row.scenario) + '">' + escapeHtml(row.scenario_label) + "</option>";
    }).join("");
    scenarioSelect.value = ui.scenario;

    var branchSelect = byId("sel-branch");
    branchSelect.innerHTML = data.branches.map(function (row) {
      return '<option value="' + escapeHtml(row.branch) + '">' +
        escapeHtml(row.branch_label) + " (" + escapeHtml(row.status) + ")</option>";
    }).join("");
    branchSelect.value = ui.branch;

    var yearSelect = byId("sel-year");
    yearSelect.innerHTML = data.years.map(function (year) {
      return '<option value="' + escapeHtml(year) + '">' + escapeHtml(year) + "</option>";
    }).join("");
    yearSelect.value = ui.year;

    var tierSelect = byId("f-tier");
    tierSelect.innerHTML = '<option value="">every kind</option>' + data.tiers.map(function (tier) {
      return '<option value="' + escapeHtml(tier) + '">' + escapeHtml(tier) + "</option>";
    }).join("");

    var bandSelect = byId("f-band");
    bandSelect.innerHTML = '<option value="">every band</option>' + data.bands.map(function (band) {
      return '<option value="' + escapeHtml(band) + '">' + escapeHtml(band) + ", " +
        escapeHtml(data.bandLabel[band] || "") + "</option>";
    }).join("");

    var flagSelect = byId("f-flag");
    flagSelect.innerHTML = '<option value="">any or none</option>' + data.flagCodes.map(function (code) {
      return '<option value="' + escapeHtml(code) + '">' + escapeHtml(code) + "</option>";
    }).join("");
  }

  function renderBranchStatus() {
    var branch = data.branches.filter(function (row) { return row.branch === ui.branch; })[0];
    if (!branch) { setHtml("branch-status", ""); return; }
    var chipClass = branch.status === "proposal" ? "chip status-proposal" : "chip status-adopted";
    var note = branch.status === "proposal"
      ? "This branch applies Commission proposals that are not law."
      : "Adopted law only.";
    setHtml("branch-status",
      '<span class="' + chipClass + '">' + escapeHtml(branch.status) + "</span> " +
      '<span class="subtitle" style="margin:0">' + escapeHtml(note) + "</span>");
  }

  // ------------------------------------------------------------- portfolio

  function activeFlagCodes(borrowerId) {
    return (data.flagsByBorrower[borrowerId] || []).map(function (row) { return row.flag; });
  }

  function hasFlag(borrowerId, code) {
    return activeFlagCodes(borrowerId).indexOf(code) !== -1;
  }

  function portfolioTotals() {
    var totalExposure = sum(data.borrowers, function (row) { return num(row.exposure_eur); });
    var ratingGap = sum(data.borrowers.filter(function (row) {
      return hasFlag(row.borrower_id, "RATING_GAP");
    }), function (row) { return num(row.exposure_eur); });
    var flagged = data.borrowers.filter(function (row) {
      return activeFlagCodes(row.borrower_id).length > 0;
    }).length;
    var estimated = data.borrowers.filter(function (row) { return isTrue(row.estimated); }).length;
    var cost = sum(data.borrowers, function (row) {
      var costRow = costFor(row.borrower_id);
      return costRow ? num(costRow.cost_eur) : 0;
    });
    return {
      exposure: totalExposure,
      ratingGap: ratingGap,
      flagged: flagged,
      estimated: estimated,
      estimatedShare: data.borrowers.length ? estimated / data.borrowers.length : null,
      cost: cost,
      count: data.borrowers.length
    };
  }

  function flagReferencePoint() {
    return "Flags were evaluated at " + (data.meta.flag_reference_year || "an unrecorded year") +
      ", " + (data.meta.flag_reference_scenario || "unrecorded scenario") +
      ", " + (data.meta.flag_reference_branch || "unrecorded branch") +
      ", so changing the selectors above does not change them.";
  }

  function renderPortfolioCards() {
    var totals = portfolioTotals();
    var cards = [
      { k: "Total exposure", v: eur(totals.exposure), n: totals.count + " borrowers, as the bank supplied it" },
      { k: "Certificate cost, selected year", v: eur(totals.cost),
        n: ui.year + ", " + scenarioLabel() + ", " + branchLabel() },
      { k: "Exposure carrying RATING_GAP", v: eur(totals.ratingGap), n: flagReferencePoint(), flagged: true },
      { k: "Borrowers with any flag", v: String(totals.flagged) + " of " + totals.count, n: "At least one flag active.", flagged: true },
      { k: "Estimated inputs", v: pct(totals.estimatedShare, 0),
        n: totals.estimated + " borrowers have at least one field filled from a sector average." }
    ];
    setHtml("portfolio-cards", cards.map(function (card) {
      return '<div class="card' + (card.flagged ? " flagged" : "") + '">' +
        '<span class="k">' + escapeHtml(card.k) + "</span>" +
        '<span class="v">' + escapeHtml(card.v) + "</span>" +
        '<span class="n">' + escapeHtml(card.n) + "</span></div>";
    }).join(""));
  }

  function scenarioLabel() {
    var row = data.scenarios.filter(function (s) { return s.scenario === ui.scenario; })[0];
    return row ? row.scenario_label : ui.scenario;
  }

  function branchLabel() {
    var row = data.branches.filter(function (b) { return b.branch === ui.branch; })[0];
    return row ? row.branch_label : ui.branch;
  }

  function renderHeatmap() {
    setHtml("heatmap-subtitle",
      "Exposure in euro, summed over the borrowers in each cell, for " + escapeHtml(ui.year) +
      ", " + escapeHtml(scenarioLabel()) + ", " + escapeHtml(branchLabel()) +
      ". Select a cell to see those borrowers.");

    var cells = {};
    var rowTotals = {};
    var colTotals = {};
    var grand = 0;
    data.borrowers.forEach(function (borrower) {
      var costRow = costFor(borrower.borrower_id);
      if (!costRow) { return; }
      var exposure = num(borrower.exposure_eur) || 0;
      var key = costRow.tier_label_plain + "|" + costRow.band;
      cells[key] = (cells[key] || 0) + exposure;
      rowTotals[costRow.tier_label_plain] = (rowTotals[costRow.tier_label_plain] || 0) + exposure;
      colTotals[costRow.band] = (colTotals[costRow.band] || 0) + exposure;
      grand += exposure;
    });

    var html = ["<caption>Rows are what a borrower is. Columns are the cost against EBITDA band the engine assigned. Every borrower appears in exactly one cell.</caption>"];
    html.push("<thead><tr><th scope=\"col\" class=\"wrap\">What it is</th>");
    data.bands.forEach(function (band) {
      html.push('<th scope="col" class="num" title="' + escapeHtml(data.bandLabel[band] || "") + '">' +
        escapeHtml(band) + "</th>");
    });
    html.push('<th scope="col" class="num">Total</th></tr></thead><tbody>');

    data.tiers.forEach(function (tier) {
      html.push('<tr><th scope="row" class="wrap">' + escapeHtml(tier) + "</th>");
      data.bands.forEach(function (band) {
        var value = cells[tier + "|" + band];
        if (!value) {
          html.push('<td class="cell empty">0</td>');
          return;
        }
        html.push('<td class="cell band-' + escapeHtml(band) + '">' +
          '<button type="button" data-tier="' + escapeHtml(tier) + '" data-band="' + escapeHtml(band) +
          '" aria-label="' + escapeHtml(tier + ", band " + band + ", " + eur(value) + ". Show these borrowers.") + '">' +
          '<span class="letter">' + escapeHtml(band) + "</span>" + EUR.format(value) + "</button></td>");
      });
      html.push('<td class="num">' + EUR.format(rowTotals[tier] || 0) + "</td></tr>");
    });

    html.push("</tbody><tfoot><tr><td>Total</td>");
    data.bands.forEach(function (band) {
      html.push('<td class="num">' + EUR.format(colTotals[band] || 0) + "</td>");
    });
    html.push('<td class="num">' + EUR.format(grand) + "</td></tr></tfoot>");
    byId("heatmap").innerHTML = html.join("");

    var declared = sum(data.borrowers, function (row) { return num(row.exposure_eur); });
    var difference = grand - declared;
    var ok = Math.abs(difference) < 0.01;
    var node = byId("reconcile");
    node.className = "reconcile " + (ok ? "ok" : "bad");
    node.innerHTML =
      "Reconciliation. Heatmap total: " + escapeHtml(eur2(grand)) +
      ". Sum of exposure in dim_borrower.csv: " + escapeHtml(eur2(declared)) +
      ". Difference: " + escapeHtml(eur2(difference)) + ". " +
      (ok ? "Every borrower is counted once and none is lost."
          : "These must match. A difference means a borrower has no cost row for this year, scenario and branch.");
  }

  function renderRangeTable() {
    // Best and worst total cost per group, across every scenario and branch,
    // in the selected year. A group that does not exist on a branch is not
    // counted as a zero there, because those borrowers are in another group
    // under that branch rather than in this one at no cost.
    var combos = {};
    (data.tables.fact_cost || []).forEach(function (row) {
      if (row.year !== ui.year) { return; }
      var key = row.tier_label_plain + "|" + row.scenario + "|" + row.branch;
      combos[key] = (combos[key] || 0) + (num(row.cost_eur) || 0);
    });

    var byTier = {};
    Object.keys(combos).forEach(function (key) {
      var parts = key.split("|");
      var tier = parts[0];
      var label = scenarioLabelOf(parts[1]) + ", " + branchLabelOf(parts[2]);
      if (!byTier[tier]) { byTier[tier] = []; }
      byTier[tier].push({ label: label, value: combos[key] });
    });

    var html = ['<thead><tr><th scope="col" class="wrap">What it is</th><th scope="col" class="num">Best case</th>' +
      '<th scope="col" class="wrap">Under</th><th scope="col" class="num">Worst case</th><th scope="col" class="wrap">Under</th></tr></thead><tbody>'];
    data.tiers.forEach(function (tier) {
      var entries = byTier[tier] || [];
      if (!entries.length) { return; }
      var best = entries[0];
      var worst = entries[0];
      entries.forEach(function (entry) {
        if (entry.value < best.value) { best = entry; }
        if (entry.value > worst.value) { worst = entry; }
      });
      html.push("<tr><th scope=\"row\" class=\"wrap\">" + escapeHtml(tier) + "</th>" +
        '<td class="num">' + escapeHtml(eur(best.value)) + "</td>" +
        '<td class="wrap">' + escapeHtml(best.label) + "</td>" +
        '<td class="num">' + escapeHtml(eur(worst.value)) + "</td>" +
        '<td class="wrap">' + escapeHtml(worst.label) + "</td></tr>");
    });
    html.push("</tbody>");
    byId("range-table").innerHTML = html.join("");
  }

  function scenarioLabelOf(key) {
    var row = data.scenarios.filter(function (s) { return s.scenario === key; })[0];
    return row ? row.scenario_label : key;
  }

  function branchLabelOf(key) {
    var row = data.branches.filter(function (b) { return b.branch === key; })[0];
    return row ? row.branch_label : key;
  }

  function renderRatingGapTable() {
    setHtml("rating-gap-subtitle",
      "Borrowers whose screening band sits far enough above the bank's own transition rating for the engine to raise RATING_GAP. " +
      flagReferencePoint());
    var rows = data.borrowers.filter(function (row) { return hasFlag(row.borrower_id, "RATING_GAP"); })
      .sort(function (a, b) { return (num(b.exposure_eur) || 0) - (num(a.exposure_eur) || 0); });
    if (!rows.length) {
      byId("rating-gap-table").innerHTML =
        "<tbody><tr><td>No borrower carries RATING_GAP at the reference point.</td></tr></tbody>";
      return;
    }
    var html = ['<thead><tr><th scope="col" class="wrap">Borrower</th><th scope="col">Bank rating</th>' +
      '<th scope="col">Screening band</th><th scope="col" class="num">Exposure</th><th scope="col" class="wrap">Why</th></tr></thead><tbody>'];
    rows.forEach(function (row) {
      var costRow = costFor(row.borrower_id);
      var band = costRow ? costRow.band : "";
      var reason = (data.flagsByBorrower[row.borrower_id] || []).filter(function (flag) {
        return flag.flag === "RATING_GAP";
      })[0];
      html.push("<tr>" + borrowerCell(row) +
        "<td>" + escapeHtml(row.bank_transition_rating || "none supplied") + "</td>" +
        "<td>" + bandChip(band) + "</td>" +
        '<td class="num">' + escapeHtml(eur(num(row.exposure_eur))) + "</td>" +
        '<td class="wrap">' + escapeHtml(reason ? reason.reason : "") + "</td></tr>");
    });
    html.push("</tbody>");
    byId("rating-gap-table").innerHTML = html.join("");
  }

  function bandChip(band) {
    if (!band) { return ""; }
    return '<span class="band band-' + escapeHtml(band) + '" title="' +
      escapeHtml(data.bandLabel[band] || "") + '">' + escapeHtml(band) + "</span>";
  }

  function borrowerCell(row) {
    return '<th scope="row" class="wrap"><button type="button" class="linklike open-borrower" data-id="' +
      escapeHtml(row.borrower_id) + '">' + escapeHtml(row.name) + "</button></th>";
  }

  function renderPortfolio() {
    renderBranchStatus();
    renderPortfolioCards();
    renderHeatmap();
    renderRangeTable();
    renderRatingGapTable();
  }

  // ------------------------------------------------------------- borrowers

  var COLUMNS = [
    { key: "name", label: "Borrower", type: "text", wrap: true },
    { key: "tier_label_plain", label: "What it is", type: "text", wrap: true },
    { key: "band", label: "Band", type: "band" },
    { key: "cost_eur", label: "Cost, selected year", type: "eur" },
    { key: "materiality_ratio", label: "Cost to EBITDA", type: "pct" },
    { key: "cash_out", label: "Cash out", type: "eur" },
    { key: "flags", label: "Flags", type: "flags", wrap: true },
    { key: "estimated", label: "Estimated", type: "est" },
    { key: "exposure_eur", label: "Exposure", type: "eur" }
  ];

  function borrowerRows() {
    return data.borrowers.map(function (borrower) {
      var costRow = costFor(borrower.borrower_id);
      var flags = activeFlagCodes(borrower.borrower_id);
      return {
        borrower_id: borrower.borrower_id,
        name: borrower.name,
        country: borrower.country,
        nace_code: borrower.nace_code,
        tier_label_plain: costRow ? costRow.tier_label_plain : borrower.tier_label_plain,
        band: costRow ? costRow.band : "",
        band_sort: costRow ? num(costRow.band_sort) : null,
        cost_eur: costRow ? num(costRow.cost_eur) : null,
        cost_basis_label: costRow ? costRow.cost_basis_label : "",
        materiality_ratio: costRow ? num(costRow.materiality_ratio) : null,
        cash_out: shockCashOut(borrower.borrower_id),
        flags: flags,
        estimated: isTrue(borrower.estimated),
        exposure_eur: num(borrower.exposure_eur),
        bank_transition_rating: borrower.bank_transition_rating
      };
    });
  }

  function filteredRows() {
    var needle = ui.search.trim().toLowerCase();
    return borrowerRows().filter(function (row) {
      if (ui.tier && row.tier_label_plain !== ui.tier) { return false; }
      if (ui.band && row.band !== ui.band) { return false; }
      if (ui.flag && row.flags.indexOf(ui.flag) === -1) { return false; }
      if (needle) {
        var hay = (row.name + " " + row.borrower_id + " " + row.country + " " + row.nace_code).toLowerCase();
        if (hay.indexOf(needle) === -1) { return false; }
      }
      return true;
    }).sort(function (a, b) {
      var key = ui.sortKey;
      var left = key === "band" ? a.band_sort : a[key];
      var right = key === "band" ? b.band_sort : b[key];
      if (key === "flags") { left = a.flags.length; right = b.flags.length; }
      if (key === "estimated") { left = a.estimated ? 1 : 0; right = b.estimated ? 1 : 0; }
      if (left === null || left === undefined) { return 1; }
      if (right === null || right === undefined) { return -1; }
      if (typeof left === "string") { return left.localeCompare(right) * ui.sortDir; }
      return (left - right) * ui.sortDir;
    });
  }

  function renderBorrowers() {
    setHtml("borrowers-subtitle",
      "Cost, band and cash out are shown for " + escapeHtml(ui.year) + ", " +
      escapeHtml(scenarioLabel()) + ", " + escapeHtml(branchLabel()) +
      ". Change those on the Portfolio view. Cash out is the total leaving the borrower in " +
      escapeHtml(shockYear()) + ", the year the engine marks as the liquidity shock.");

    var rows = filteredRows();
    setHtml("borrowers-count", rows.length + " of " + data.borrowers.length +
      " borrowers shown. Select a name for the detail view.");

    var html = ["<thead><tr>"];
    COLUMNS.forEach(function (column) {
      var arrow = ui.sortKey === column.key ? (ui.sortDir === 1 ? "up" : "down") : "";
      html.push('<th scope="col" class="' + (column.wrap ? "wrap" : "") +
        (column.type === "eur" || column.type === "pct" ? " num" : "") + '"' +
        (ui.sortKey === column.key ? ' aria-sort="' + (ui.sortDir === 1 ? "ascending" : "descending") + '"' : "") +
        '><button type="button" class="sort" data-key="' + column.key + '">' +
        escapeHtml(column.label) +
        (arrow ? ' <span class="arrow" aria-hidden="true">' + (arrow === "up" ? "▲" : "▼") + "</span>" : "") +
        "</button></th>");
    });
    html.push("</tr></thead><tbody>");

    if (!rows.length) {
      html.push('<tr><td colspan="' + COLUMNS.length + '">No borrower matches these filters.</td></tr>');
    }

    rows.forEach(function (row) {
      html.push("<tr>");
      html.push(borrowerCell(row));
      html.push('<td class="wrap">' + escapeHtml(row.tier_label_plain) + "</td>");
      html.push("<td>" + bandChip(row.band) + "</td>");
      html.push('<td class="num" title="' + escapeHtml(row.cost_basis_label) + '">' +
        escapeHtml(row.cost_eur === null ? "no row" : eur(row.cost_eur)) + "</td>");
      html.push('<td class="num">' + escapeHtml(row.materiality_ratio === null ? "no ratio" : pct(row.materiality_ratio)) + "</td>");
      html.push('<td class="num">' + escapeHtml(eur(row.cash_out)) + "</td>");
      html.push('<td class="wrap">' + (row.flags.length
        ? row.flags.map(function (code) { return '<span class="chip flag">' + escapeHtml(code) + "</span>"; }).join("")
        : '<span class="chip">none</span>') + "</td>");
      html.push("<td>" + (row.estimated ? '<span class="chip est">estimated</span>' : "") + "</td>");
      html.push('<td class="num">' + escapeHtml(eur(row.exposure_eur)) + "</td>");
      html.push("</tr>");
    });
    html.push("</tbody>");
    byId("borrowers-table").innerHTML = html.join("");
  }

  function downloadFilteredCsv() {
    var rows = filteredRows().map(function (row) {
      return {
        borrower_id: row.borrower_id,
        name: row.name,
        country: row.country,
        nace_code: row.nace_code,
        what_it_is: row.tier_label_plain,
        band: row.band,
        cost_eur: row.cost_eur === null ? "" : row.cost_eur,
        cost_basis: row.cost_basis_label,
        materiality_ratio: row.materiality_ratio === null ? "" : row.materiality_ratio,
        cash_out_shock_year_eur: row.cash_out,
        flags: row.flags.join(" "),
        estimated: row.estimated ? "true" : "false",
        exposure_eur: row.exposure_eur,
        bank_transition_rating: row.bank_transition_rating,
        year: ui.year,
        scenario: ui.scenario,
        branch: ui.branch,
        data_label: data.meta.data_label || "",
        generated_at: data.meta.generated_at || ""
      };
    });
    var header = ["borrower_id", "name", "country", "nace_code", "what_it_is", "band",
      "cost_eur", "cost_basis", "materiality_ratio", "cash_out_shock_year_eur", "flags",
      "estimated", "exposure_eur", "bank_transition_rating", "year", "scenario", "branch",
      "data_label", "generated_at"];
    // The label travels with the file. A number in a credit file with no label
    // on it is the outcome this whole project exists to avoid.
    var banner = "# " + (data.meta.data_label || "UNLABELLED") + " DATA. " + dataWarning() +
      " Exported " + (data.meta.generated_at || "") + ". Filtered view from the CBAM screening dashboard.\n";
    var blob = new Blob([banner + toCsv(header, rows)], { type: "text/csv;charset=utf-8" });
    var url = URL.createObjectURL(blob);
    var anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "cbam_borrowers_" + (data.meta.data_label || "export").toLowerCase() + "_" +
      ui.scenario + "_" + ui.branch + "_" + ui.year + ".csv";
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  // ----------------------------------------------------------------- charts

  var SERIES_COLOURS = ["#a8c0b0", "#e0b061", "#4ade80"];
  var SERIES_DASH = ["6 3", "2 3", "0"];

  function lineChart(series, options) {
    var width = 720;
    var height = 280;
    var left = 78;
    var right = 14;
    var top = 14;
    var bottom = 34;
    var xs = options.xs;
    var maxima = [0];
    series.forEach(function (line) {
      line.values.forEach(function (value) { maxima.push(value); });
    });
    var maxValue = Math.max.apply(null, maxima);
    if (maxValue <= 0) { maxValue = 1; }
    var plotWidth = width - left - right;
    var plotHeight = height - top - bottom;

    function px(index) {
      return left + (xs.length === 1 ? plotWidth / 2 : (index / (xs.length - 1)) * plotWidth);
    }
    function py(value) {
      return top + plotHeight - (value / maxValue) * plotHeight;
    }

    var parts = ['<svg class="chart" viewBox="0 0 ' + width + " " + height +
      '" role="img" aria-label="' + escapeHtml(options.ariaLabel) + '">'];
    parts.push('<g class="grid">');
    for (var t = 0; t <= 4; t += 1) {
      var value = (maxValue / 4) * t;
      var y = py(value);
      parts.push('<line x1="' + left + '" y1="' + y + '" x2="' + (width - right) + '" y2="' + y + '"></line>');
      parts.push('<text x="' + (left - 8) + '" y="' + (y + 3) + '" text-anchor="end">' +
        escapeHtml(EUR.format(value)) + "</text>");
    }
    parts.push("</g>");
    parts.push('<g class="axis"><line x1="' + left + '" y1="' + (top + plotHeight) + '" x2="' +
      (width - right) + '" y2="' + (top + plotHeight) + '"></line></g>');
    xs.forEach(function (label, index) {
      parts.push('<text x="' + px(index) + '" y="' + (height - 12) + '" text-anchor="middle">' +
        escapeHtml(label) + "</text>");
    });
    series.forEach(function (line, lineIndex) {
      var points = line.values.map(function (value, index) {
        return px(index).toFixed(1) + "," + py(value).toFixed(1);
      }).join(" ");
      parts.push('<polyline fill="none" stroke="' + SERIES_COLOURS[lineIndex % 3] +
        '" stroke-width="2" stroke-dasharray="' + SERIES_DASH[lineIndex % 3] +
        '" points="' + points + '"></polyline>');
      line.values.forEach(function (value, index) {
        parts.push('<circle cx="' + px(index).toFixed(1) + '" cy="' + py(value).toFixed(1) +
          '" r="2.5" fill="' + SERIES_COLOURS[lineIndex % 3] + '"><title>' +
          escapeHtml(line.label + ", " + xs[index] + ": " + eur(value)) + "</title></circle>");
      });
    });
    parts.push("</svg>");

    var legend = ['<div class="legend">'];
    series.forEach(function (line, index) {
      legend.push('<span><span class="swatch" style="background:' + SERIES_COLOURS[index % 3] +
        '"></span>' + escapeHtml(line.label) + "</span>");
    });
    legend.push("</div>");
    return parts.join("") + legend.join("");
  }

  function barChart(bars, options) {
    var width = 720;
    var height = 240;
    var left = 78;
    var right = 14;
    var top = 14;
    var bottom = 52;
    var plotWidth = width - left - right;
    var plotHeight = height - top - bottom;
    var maxValue = Math.max.apply(null, [0].concat(bars.map(function (bar) { return bar.value; })));
    if (maxValue <= 0) { maxValue = 1; }
    var slot = bars.length ? plotWidth / bars.length : plotWidth;
    var barWidth = Math.min(64, slot * 0.6);

    var parts = ['<svg class="chart" viewBox="0 0 ' + width + " " + height +
      '" role="img" aria-label="' + escapeHtml(options.ariaLabel) + '">'];
    parts.push('<g class="grid">');
    for (var t = 0; t <= 4; t += 1) {
      var value = (maxValue / 4) * t;
      var y = top + plotHeight - (value / maxValue) * plotHeight;
      parts.push('<line x1="' + left + '" y1="' + y + '" x2="' + (width - right) + '" y2="' + y + '"></line>');
      parts.push('<text x="' + (left - 8) + '" y="' + (y + 3) + '" text-anchor="end">' +
        escapeHtml(EUR.format(value)) + "</text>");
    }
    parts.push("</g>");
    bars.forEach(function (bar, index) {
      var barHeight = (bar.value / maxValue) * plotHeight;
      var x = left + slot * index + (slot - barWidth) / 2;
      var y = top + plotHeight - barHeight;
      parts.push('<rect x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + barWidth.toFixed(1) +
        '" height="' + Math.max(barHeight, 0).toFixed(1) + '" fill="' + (bar.colour || "#4ade80") +
        '"><title>' + escapeHtml(bar.label + ": " + eur(bar.value)) + "</title></rect>");
      parts.push('<text x="' + (left + slot * index + slot / 2).toFixed(1) + '" y="' + (height - 30) +
        '" text-anchor="middle">' + escapeHtml(bar.short) + "</text>");
      parts.push('<text x="' + (left + slot * index + slot / 2).toFixed(1) + '" y="' + (height - 16) +
        '" text-anchor="middle">' + escapeHtml(EUR.format(bar.value)) + "</text>");
    });
    parts.push('<g class="axis"><line x1="' + left + '" y1="' + (top + plotHeight) + '" x2="' +
      (width - right) + '" y2="' + (top + plotHeight) + '"></line></g>');
    parts.push("</svg>");
    return parts.join("");
  }

  // ------------------------------------------------------------ the detail

  function renderDetail() {
    var node = byId("detail-body");
    if (!ui.borrowerId || !data.borrowerById[ui.borrowerId]) {
      node.innerHTML = "<h1>Borrower detail</h1><p class=\"subtitle\">Pick a borrower on the Borrowers view, or select a heatmap cell on the Portfolio view and then a name.</p>";
      return;
    }
    var borrower = data.borrowerById[ui.borrowerId];
    var costRow = costFor(ui.borrowerId);
    var flags = data.flagsByBorrower[ui.borrowerId] || [];

    var html = [];
    html.push("<h1>" + escapeHtml(borrower.name) + "</h1>");
    html.push('<p class="subtitle">' + escapeHtml(borrower.borrower_id) + ", NACE " +
      escapeHtml(borrower.nace_code) + ", " + escapeHtml(borrower.country) +
      ". Shown for " + escapeHtml(ui.year) + ", " + escapeHtml(scenarioLabel()) + ", " +
      escapeHtml(branchLabel()) + ".</p>");

    html.push('<div class="cards">');
    html.push(card("What it is", costRow ? costRow.tier_label_plain : "no cost row", borrower.tier_reason || ""));
    html.push(card("Exposure", eur(num(borrower.exposure_eur)), "As the bank supplied it."));
    html.push(card("Certificate cost", costRow ? eur(num(costRow.cost_eur)) : "no row",
      costRow ? costRow.cost_basis_label : ""));
    var ebitda = num(borrower.ebitda_eur);
    html.push(card("Cost against EBITDA",
      costRow && costRow.materiality_ratio ? pct(num(costRow.materiality_ratio)) : "no ratio",
      costRow
        ? "Band " + costRow.band + ", " + costRow.band_label +
          (costRow.materiality_ratio ? "" :
            ". EBITDA is " + eur(ebitda) + ", so the ratio has no meaning and the band was set by the rule in config/thresholds.yaml.")
        : ""));
    html.push(card("Bank transition rating", borrower.bank_transition_rating || "none supplied",
      "Supplied by the bank, not computed here."));
    html.push("</div>");

    // Cost path, all three scenarios, on the selected branch.
    var years = data.years;
    var series = data.scenarios.map(function (scenario) {
      return {
        label: scenario.scenario_label,
        values: years.map(function (year) {
          var row = data.costByKey[ui.borrowerId + "|" + year + "|" + scenario.scenario + "|" + ui.branch];
          return row ? (num(row.cost_eur) || 0) : 0;
        })
      };
    });
    html.push('<div class="panel"><h2>Certificate cost path</h2>');
    html.push('<p class="subtitle">' + escapeHtml(years[0] + " to " + years[years.length - 1]) +
      ", all three carbon-price scenarios, on " + escapeHtml(branchLabel()) +
      ". Every point is a row in fact_cost.csv.</p>");
    html.push(lineChart(series, {
      xs: years,
      ariaLabel: "Certificate cost by year for " + borrower.name + " under three carbon price scenarios"
    }));
    html.push("</div>");

    // Liquidity in the shock year.
    var liquidity = (data.liquidityByBorrower[ui.borrowerId] || []).filter(function (row) {
      return row.scenario === ui.scenario && row.branch === ui.branch && row.year === shockYear();
    }).sort(function (a, b) {
      return (a.quarter || "0").localeCompare(b.quarter || "0");
    });
    html.push('<div class="panel"><h2>Cash out in ' + escapeHtml(shockYear()) + "</h2>");
    if (!liquidity.length) {
      html.push('<p class="subtitle">This borrower has no payment rows in ' + escapeHtml(shockYear()) +
        " for this scenario and branch. A borrower that owes nothing has no payment rows at all, rather than a row of zeros.</p>");
    } else {
      var capitalShare = liquidity[0].share_of_working_capital;
      html.push('<p class="subtitle">The ' + escapeHtml(String(Number(shockYear()) - 1)) +
        " obligation cannot be paid before certificates go on sale, so it lands as one block, while the " +
        escapeHtml(shockYear()) + " obligation already prepays in the same year. Two obligations in one year is the shock.</p>");
      html.push(barChart(liquidity.map(function (row) {
        return {
          label: row.window + ", " + row.kind,
          short: row.window.replace(shockYear() + " ", ""),
          value: num(row.cash_out_eur) || 0,
          colour: row.kind === "block" ? "#e0b061" : "#4ade80"
        };
      }), { ariaLabel: "Cash out by payment window in " + shockYear() + " for " + borrower.name }));
      html.push('<p class="subtitle">Total ' + escapeHtml(eur(sum(liquidity, function (row) {
        return num(row.cash_out_eur);
      }))) + (capitalShare
        ? ", against working capital of " + escapeHtml(eur(num(borrower.working_capital_eur))) + "."
        : ". No working capital was supplied, so no share of it is shown rather than a guessed one.") + "</p>");
    }
    html.push("</div>");

    html.push(whyThisRating(borrower, costRow));
    html.push(whatToAsk(flags));
    node.innerHTML = html.join("");
  }

  function card(key, value, note) {
    return '<div class="card"><span class="k">' + escapeHtml(key) + '</span><span class="v">' +
      escapeHtml(value) + '</span><span class="n">' + escapeHtml(note) + "</span></div>";
  }

  function whyThisRating(borrower, costRow) {
    var html = ['<div class="panel"><h2>Why this rating</h2>'];
    if (!costRow) {
      html.push('<p class="subtitle">No cost row for this year, scenario and branch.</p></div>');
      return html.join("");
    }
    var lines = linesFor(borrower.borrower_id);
    html.push('<p class="subtitle">The engine\'s formula, with this borrower\'s own numbers. Every value below is a column in fact_cost_line.csv and fact_cost.csv.</p>');

    if (!lines.length) {
      if (!data.tables.fact_cost_line) {
        html.push('<div class="note amber">fact_cost_line.csv was not loaded, so the cost cannot be broken down into the formula. Re-run the exporter and load the whole export folder.</div>');
      } else {
        html.push('<div class="note amber">' + escapeHtml(costRow.cost_basis_label) +
          ". There is no cost line to multiply out, so the cost shown is " +
          escapeHtml(eur(num(costRow.cost_eur))) + ". " +
          (costRow.materiality_ratio
            ? "Cost against EBITDA is " + escapeHtml(pct(num(costRow.materiality_ratio))) +
              ", which is band " + escapeHtml(costRow.band) + "."
            : "EBITDA is " + escapeHtml(eur(num(borrower.ebitda_eur))) +
              ", so there is no meaningful ratio and band " + escapeHtml(costRow.band) +
              " was set by the rule in config/thresholds.yaml.") +
          "</div>");
      }
    } else {
      var body = [];
      lines.forEach(function (line) {
        var multiplier = line.cost_basis === "lost_allocation"
          ? plain(num(line.charged_share)) + "  (" + line.charged_share_label + ")"
          : "(1 - " + plain(num(line.free_allocation_share)) + ")";
        body.push(line.good_group + ":  " +
          plain(num(line.quantity)) + " t  x  " +
          plain(num(line.emission_factor)) + " tCO2 per t  x  EUR " +
          plain(num(line.price_eur)) + "  x  " + multiplier +
          "  =  " + eur2(num(line.cost_eur)) +
          "\n    emission factor basis: " + line.factor_basis);
      });
      body.push("");
      body.push("total cost  =  " + eur2(num(costRow.cost_eur)));
      if (costRow.materiality_ratio) {
        body.push("cost / EBITDA  =  " + eur2(num(costRow.cost_eur)) + " / " +
          eur2(num(borrower.ebitda_eur)) + "  =  " + pct(num(costRow.materiality_ratio)) +
          "  ->  band " + costRow.band + ", " + costRow.band_label);
      } else {
        body.push("EBITDA is " + eur2(num(borrower.ebitda_eur)) +
          ", so there is no meaningful ratio and the band was set by the rule in config: " +
          costRow.band + ", " + costRow.band_label);
      }
      html.push('<pre class="formula">' + escapeHtml(body.join("\n")) + "</pre>");
    }

    html.push('<div class="note"><p>Counted tonnes against the mass threshold: ' +
      escapeHtml(plain(num(costRow.counted_tonnes))) + " of " +
      escapeHtml(plain(num(costRow.threshold_tonnes))) + ", so this borrower is " +
      (isTrue(costRow.below_threshold) ? "below it and owes nothing this year" : "above it") + ".</p>");
    html.push("<p>Carbon price: " + escapeHtml(eur2(num(costRow.price_eur))) + " per tonne, from " +
      escapeHtml(data.meta.price_source_label || "the price source in meta.csv") +
      ". Free allocation still granted in " + escapeHtml(ui.year) + ": " +
      escapeHtml(pct(num(costRow.free_allocation_share), 1)) + ", from config/cbam_rules.yaml.</p>");
    html.push("<p>Tier reason: " + escapeHtml(borrower.tier_reason || "not recorded") + "</p>");
    if (isTrue(borrower.estimated)) {
      html.push("<p>Estimated inputs, filled from sector averages: " +
        escapeHtml(borrower.estimated_fields || "") + ". The cost is indicative only.</p>");
    }
    html.push("<p>Band boundaries are the author's assumptions in config/thresholds.yaml, not law and not supervisory guidance.</p></div></div>");
    return html.join("");
  }

  function whatToAsk(flags) {
    var html = ['<div class="panel"><h2>What to ask this client</h2>'];
    html.push('<p class="subtitle">' + escapeHtml(flagReferencePoint()) + "</p>");
    if (!flags.length) {
      html.push("<p>No flag is active for this borrower, so the tool has nothing to ask.</p></div>");
      return html.join("");
    }
    if (!data.tables.questions) {
      html.push('<div class="note amber">questions.csv was not loaded, so only the engine\'s own reasons are shown.</div>');
    }
    html.push('<dl class="qa">');
    flags.slice().sort(function (a, b) { return a.flag.localeCompare(b.flag); }).forEach(function (flag) {
      var questions = data.questionsByFlag[flag.flag] || [];
      html.push("<dt>" + escapeHtml(flag.flag) + "</dt><dd>");
      html.push('<div class="reason">' + escapeHtml(flag.reason) + "</div>");
      if (questions.length) {
        html.push("<ul>");
        questions.slice().sort(function (a, b) {
          return num(a.question_order) - num(b.question_order);
        }).forEach(function (question) {
          html.push("<li>" + escapeHtml(question.question_text) + "</li>");
        });
        html.push("</ul>");
      }
      html.push("</dd>");
    });
    html.push("</dl>");
    html.push('<p class="subtitle">The questions are the author\'s assumptions in config/questions.yaml, not law and not supervisory guidance.</p></div>');
    return html.join("");
  }

  // ----------------------------------------------------------------- summary

  function renderSummary() {
    var totals = portfolioTotals();
    var rows = borrowerRows().slice().sort(function (a, b) {
      if (a.materiality_ratio === null) { return 1; }
      if (b.materiality_ratio === null) { return -1; }
      return b.materiality_ratio - a.materiality_ratio;
    });
    var top = rows.slice(0, 20);

    var flagCounts = data.flagCodes.map(function (code) {
      return {
        code: code,
        count: data.borrowers.filter(function (row) { return hasFlag(row.borrower_id, code); }).length
      };
    }).sort(function (a, b) { return b.count - a.count; });

    // Total cost across every scenario and branch in the selected year, so the
    // committee sees the range rather than one point.
    var comboTotals = {};
    (data.tables.fact_cost || []).forEach(function (row) {
      if (row.year !== ui.year) { return; }
      var key = row.scenario + "|" + row.branch;
      comboTotals[key] = (comboTotals[key] || 0) + (num(row.cost_eur) || 0);
    });
    var comboKeys = Object.keys(comboTotals);
    var best = comboKeys[0];
    var worst = comboKeys[0];
    comboKeys.forEach(function (key) {
      if (comboTotals[key] < comboTotals[best]) { best = key; }
      if (comboTotals[key] > comboTotals[worst]) { worst = key; }
    });

    var html = [];
    html.push("<h1>CBAM screening summary</h1>");
    html.push('<p class="subtitle">' + escapeHtml(data.meta.data_label || "UNLABELLED") +
      " data, exported " + escapeHtml(data.meta.generated_at || "") +
      ". Carbon prices: " + escapeHtml(data.meta.price_source_label || "unknown") +
      ". Shown for " + escapeHtml(ui.year) + ", " + escapeHtml(scenarioLabel()) + ", " +
      escapeHtml(branchLabel()) + ".</p>");

    html.push('<div class="cards">');
    html.push(card("Total exposure", eur(totals.exposure), totals.count + " borrowers"));
    html.push(card("Certificate cost", eur(totals.cost), ui.year + ", selected scenario and branch"));
    html.push(card("Cash out in " + shockYear(), eur(sum(data.borrowers, function (row) {
      return shockCashOut(row.borrower_id);
    })), "Across the portfolio"));
    html.push(card("Exposure carrying RATING_GAP", eur(totals.ratingGap), "See the flag reference point below"));
    html.push(card("Borrowers with any flag", String(totals.flagged) + " of " + totals.count,
      pct(totals.estimatedShare, 0) + " have an estimated input"));
    html.push("</div>");

    html.push('<div class="panel"><h2>The range across scenarios and branches</h2>');
    html.push('<p class="subtitle">Total certificate cost for the whole portfolio in ' + escapeHtml(ui.year) +
      ", under each of the six combinations.</p>");
    html.push('<div class="scroll-x"><table><thead><tr><th scope="col">Case</th><th scope="col" class="wrap">Scenario and branch</th><th scope="col" class="num">Total cost</th></tr></thead><tbody>');
    if (comboKeys.length) {
      html.push("<tr><th scope=\"row\">Best</th><td class=\"wrap\">" +
        escapeHtml(scenarioLabelOf(best.split("|")[0]) + ", " + branchLabelOf(best.split("|")[1])) +
        '</td><td class="num">' + escapeHtml(eur(comboTotals[best])) + "</td></tr>");
      html.push("<tr><th scope=\"row\">Worst</th><td class=\"wrap\">" +
        escapeHtml(scenarioLabelOf(worst.split("|")[0]) + ", " + branchLabelOf(worst.split("|")[1])) +
        '</td><td class="num">' + escapeHtml(eur(comboTotals[worst])) + "</td></tr>");
    }
    html.push("</tbody></table></div></div>");

    html.push('<div class="panel"><h2>Top 20 borrowers by cost against EBITDA</h2>');
    html.push('<div class="scroll-x"><table><thead><tr><th scope="col" class="num">Rank</th>' +
      '<th scope="col" class="wrap">Borrower</th><th scope="col" class="wrap">What it is</th>' +
      '<th scope="col">Band</th><th scope="col" class="num">Cost</th><th scope="col" class="num">Cost to EBITDA</th>' +
      '<th scope="col" class="num">Exposure</th><th scope="col" class="wrap">Flags</th></tr></thead><tbody>');
    top.forEach(function (row, index) {
      html.push('<tr><td class="num">' + (index + 1) + "</td>" +
        '<td class="wrap">' + escapeHtml(row.name) + "</td>" +
        '<td class="wrap">' + escapeHtml(row.tier_label_plain) + "</td>" +
        "<td>" + bandChip(row.band) + "</td>" +
        '<td class="num">' + escapeHtml(row.cost_eur === null ? "" : eur(row.cost_eur)) + "</td>" +
        '<td class="num">' + escapeHtml(row.materiality_ratio === null ? "no ratio" : pct(row.materiality_ratio)) + "</td>" +
        '<td class="num">' + escapeHtml(eur(row.exposure_eur)) + "</td>" +
        '<td class="wrap">' + escapeHtml(row.flags.join(" ")) + "</td></tr>");
    });
    html.push("</tbody></table></div></div>");

    html.push('<div class="panel"><h2>Flags raised</h2>');
    html.push('<p class="subtitle">' + escapeHtml(flagReferencePoint()) + "</p>");
    html.push('<div class="scroll-x"><table><thead><tr><th scope="col">Flag</th><th scope="col" class="num">Borrowers</th></tr></thead><tbody>');
    flagCounts.forEach(function (entry) {
      html.push("<tr><td>" + escapeHtml(entry.code) + '</td><td class="num">' + entry.count + "</td></tr>");
    });
    html.push("</tbody></table></div></div>");

    html.push('<div class="panel"><h2>Method note</h2><div class="note">');
    html.push("<p>Costs are estimates from sector defaults and default emission values unless a borrower's supplier data is marked verified. Materiality bands are the author's assumptions in config/thresholds.yaml, not law and not supervisory guidance. The proposal branch applies two Commission proposals that are not law. " +
      escapeHtml(data.meta.tier_4_cost_note || "") + "</p>");
    html.push("<p>Data label: <strong>" + escapeHtml(data.meta.data_label || "UNLABELLED") +
      "</strong>. Exported " + escapeHtml(data.meta.generated_at || "") + " from engine commit " +
      escapeHtml(data.meta.engine_commit || "unknown") + ". Carbon prices: " +
      escapeHtml(data.meta.price_source || "unknown") + ". Borrower list: " +
      escapeHtml(data.meta.borrower_source || "unknown") + ".</p>");
    html.push("</div></div>");

    byId("summary-body").innerHTML = html.join("");
  }

  // -------------------------------------------------------------- loading

  function loadFromFolder() {
    var found = {};
    var missing = [];
    var work = ALL_TABLES.map(function (name) {
      return fetch("data/" + name + ".csv", { cache: "no-store" })
        .then(function (response) {
          if (!response.ok) { throw new Error(String(response.status)); }
          return response.text();
        })
        .then(function (text) { found[name] = parseCsv(text); })
        .catch(function () { missing.push(name); });
    });
    return Promise.all(work).then(function () {
      return { tables: found, missing: missing };
    });
  }

  function readFiles(fileList) {
    var files = Array.prototype.slice.call(fileList);
    var found = {};
    var ignored = [];
    var work = files.map(function (file) {
      var stem = file.name.replace(/\.csv$/i, "").toLowerCase();
      if (ALL_TABLES.indexOf(stem) === -1) {
        ignored.push(file.name);
        return Promise.resolve();
      }
      return file.text().then(function (text) { found[stem] = parseCsv(text); });
    });
    return Promise.all(work).then(function () {
      return { tables: found, ignored: ignored };
    });
  }

  function validationReport(tables, ignored, accepted) {
    var html = ["<h2>What was read</h2>"];
    html.push("<ul>");
    ALL_TABLES.forEach(function (name) {
      var rows = tables[name];
      var optional = OPTIONAL_TABLES.indexOf(name) !== -1;
      if (rows) {
        html.push('<li><span class="ok">Found</span> ' + escapeHtml(name) + ".csv, " +
          rows.length + " rows.</li>");
      } else {
        html.push('<li><span class="missing">Missing</span> ' + escapeHtml(name) + ".csv." +
          (optional ? " This one is optional. Without it the formula box or the question box says so."
                    : " This one is needed, so the file set was not loaded.") + "</li>");
      }
    });
    html.push("</ul>");
    if (ignored && ignored.length) {
      html.push("<p>Ignored, because they are not part of the export: " +
        escapeHtml(ignored.join(", ")) + ".</p>");
    }
    var meta = {};
    (tables.meta || []).forEach(function (row) { meta[row.key] = row.value; });
    if (meta.data_label) {
      html.push("<p>Data label found in meta.csv: <strong>" + escapeHtml(meta.data_label) +
        "</strong>, exported " + escapeHtml(meta.generated_at || "at an unrecorded time") +
        ", carbon prices " + escapeHtml(meta.price_source_label || "unknown") +
        ", " + escapeHtml(meta.borrower_count || "an unrecorded number of") + " borrowers.</p>");
    }
    html.push(accepted
      ? '<p class="ok">Loaded. Every view now shows this file set. Nothing was uploaded anywhere.</p>'
      : '<p class="missing">Not loaded. The views still show the file set that was already open.</p>');
    return html.join("");
  }

  function acceptTables(tables, sourceNote) {
    var complete = REQUIRED_TABLES.every(function (name) { return Boolean(tables[name]); });
    if (!complete) { return false; }
    data = buildDataset(tables, sourceNote);
    var defaultScenario = (data.scenarios.filter(function (row) { return isTrue(row.is_default); })[0]
      || data.scenarios[0] || {}).scenario;
    var defaultBranch = (data.branches.filter(function (row) { return isTrue(row.is_default); })[0]
      || data.branches[0] || {}).branch;
    var shock = data.meta.liquidity_shock_year;
    ui.scenario = defaultScenario;
    ui.branch = defaultBranch;
    ui.year = data.years.indexOf(shock) !== -1 ? shock : data.years[0];
    ui.borrowerId = null;
    ui.search = "";
    ui.tier = "";
    ui.band = "";
    ui.flag = "";
    renderBanner();
    renderFooter();
    fillSelectors();
    renderAll();
    return true;
  }

  function renderAll() {
    renderPortfolio();
    renderBorrowers();
    renderDetail();
    renderSummary();
  }

  // ---------------------------------------------------------------- events

  function selectView(view) {
    ui.view = view;
    ["portfolio", "borrowers", "detail", "summary", "data"].forEach(function (name) {
      var tab = byId("tab-" + name);
      var panel = byId("panel-" + name);
      var on = name === view;
      tab.setAttribute("aria-selected", on ? "true" : "false");
      tab.tabIndex = on ? 0 : -1;
      panel.hidden = !on;
    });
    var panel = byId("panel-" + view);
    if (panel) { panel.focus(); }
  }

  function wireTabs() {
    var tabs = Array.prototype.slice.call(document.querySelectorAll('[role="tab"]'));
    tabs.forEach(function (tab) {
      tab.addEventListener("click", function () { selectView(tab.dataset.view); });
      tab.addEventListener("keydown", function (event) {
        var index = tabs.indexOf(tab);
        var next = null;
        if (event.key === "ArrowRight") { next = tabs[(index + 1) % tabs.length]; }
        if (event.key === "ArrowLeft") { next = tabs[(index - 1 + tabs.length) % tabs.length]; }
        if (event.key === "Home") { next = tabs[0]; }
        if (event.key === "End") { next = tabs[tabs.length - 1]; }
        if (next) {
          event.preventDefault();
          next.focus();
          selectView(next.dataset.view);
        }
      });
    });
  }

  function wireControls() {
    byId("sel-scenario").addEventListener("change", function (event) {
      ui.scenario = event.target.value;
      renderAll();
    });
    byId("sel-branch").addEventListener("change", function (event) {
      ui.branch = event.target.value;
      renderAll();
    });
    byId("sel-year").addEventListener("change", function (event) {
      ui.year = event.target.value;
      renderAll();
    });
    byId("f-search").addEventListener("input", function (event) {
      ui.search = event.target.value;
      renderBorrowers();
    });
    byId("f-tier").addEventListener("change", function (event) {
      ui.tier = event.target.value;
      renderBorrowers();
    });
    byId("f-band").addEventListener("change", function (event) {
      ui.band = event.target.value;
      renderBorrowers();
    });
    byId("f-flag").addEventListener("change", function (event) {
      ui.flag = event.target.value;
      renderBorrowers();
    });
    byId("btn-clear").addEventListener("click", function () {
      ui.search = "";
      ui.tier = "";
      ui.band = "";
      ui.flag = "";
      byId("f-search").value = "";
      byId("f-tier").value = "";
      byId("f-band").value = "";
      byId("f-flag").value = "";
      renderBorrowers();
    });
    byId("btn-csv").addEventListener("click", downloadFilteredCsv);
    byId("btn-print").addEventListener("click", function () { window.print(); });

    document.addEventListener("click", function (event) {
      var open = event.target.closest ? event.target.closest(".open-borrower") : null;
      if (open) {
        ui.borrowerId = open.dataset.id;
        renderDetail();
        selectView("detail");
        return;
      }
      var cell = event.target.closest ? event.target.closest(".heatmap button") : null;
      if (cell) {
        ui.tier = cell.dataset.tier;
        ui.band = cell.dataset.band;
        ui.search = "";
        byId("f-tier").value = ui.tier;
        byId("f-band").value = ui.band;
        byId("f-search").value = "";
        renderBorrowers();
        selectView("borrowers");
        return;
      }
      var sorter = event.target.closest ? event.target.closest("button.sort") : null;
      if (sorter) {
        var key = sorter.dataset.key;
        if (ui.sortKey === key) {
          ui.sortDir = -ui.sortDir;
        } else {
          ui.sortKey = key;
          ui.sortDir = key === "name" || key === "tier_label_plain" ? 1 : -1;
        }
        renderBorrowers();
      }
    });
  }

  function wireDropZone() {
    var zone = byId("dropzone");
    var input = byId("file-input");

    function handle(files) {
      readFiles(files).then(function (result) {
        var accepted = acceptTables(result.tables, "files you selected in this browser");
        setHtml("load-report", validationReport(result.tables, result.ignored, accepted));
        if (accepted) { selectView("portfolio"); }
      });
    }

    ["dragenter", "dragover"].forEach(function (name) {
      zone.addEventListener(name, function (event) {
        event.preventDefault();
        zone.classList.add("over");
      });
    });
    ["dragleave", "drop"].forEach(function (name) {
      zone.addEventListener(name, function (event) {
        event.preventDefault();
        zone.classList.remove("over");
      });
    });
    zone.addEventListener("drop", function (event) {
      if (event.dataTransfer && event.dataTransfer.files) { handle(event.dataTransfer.files); }
    });
    input.addEventListener("change", function (event) { handle(event.target.files); });
  }

  // ------------------------------------------------------------------ boot

  function start() {
    wireTabs();
    wireControls();
    wireDropZone();
    loadFromFolder().then(function (result) {
      var accepted = acceptTables(result.tables, "the export folder shipped with this page, at data/");
      setHtml("load-report", validationReport(result.tables, [], accepted));
      if (!accepted) {
        byId("data-banner").innerHTML =
          "<strong>NO DATA.</strong> The export folder at data/ is missing " +
          escapeHtml(result.missing.join(", ")) +
          ". Open the Your own data tab and select the csv files from an export folder.";
        selectView("data");
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
}());
