/* The Screen a borrower view.
 *
 * What this file does: collect what an analyst types, hand it to the Python
 * engine running in this browser tab under Pyodide, and draw the answer.
 *
 * What it must never do: work out a CBAM number. There is no cost formula, no
 * band boundary, no threshold, no price, no emission factor and no flag rule
 * anywhere below. Every figure on the card is a value the engine returned, or a
 * sum, a count or a lookup over those values. The engine is src/engine_api.py,
 * byte for byte the file pytest runs, fetched from webapp/engine/ and checked
 * against webapp/cfg/manifest.json before it is used.
 *
 * Even the form's own option lists come from the engine: the NACE codes, the
 * country lists, which goods have a box and which borrower column each box
 * writes into all come out of form_options_json, so a change in config changes
 * the form without a change here.
 *
 * This file uses async and await, unlike app.js. Pyodide needs a browser that
 * has them anyway, and a promise chain around the file writes would be harder
 * to read than the thing it replaced.
 */

/* eslint-env browser */
(function () {
  "use strict";

  // Pinned on purpose. An unpinned CDN path would change the Python runtime
  // under the page without anyone deciding to.
  var PYODIDE_VERSION = "0.28.3";
  var PYODIDE_BASE = "https://cdn.jsdelivr.net/pyodide/v" + PYODIDE_VERSION + "/full/";

  // pyyaml because every rule is in yaml, pydantic because schema.py validates
  // the borrower row with it. Nothing else: the in-browser engine path has no
  // pandas on it, and a test in tests/test_webapp_bundle.py keeps it that way.
  var PYODIDE_PACKAGES = ["pyyaml", "pydantic"];

  var FS_ROOT = "/cbam";
  var ENGINE_PATH = FS_ROOT + "/engine";
  var CONFIG_DIR = FS_ROOT + "/cfg";
  var PRICES_FILE = CONFIG_DIR + "/prices_fixture.yaml";

  var U = window.CBAM || {};
  var esc = U.escapeHtml || function (v) { return String(v); };
  var num = U.num || function (v) { return v === "" ? null : Number(v); };

  var engine = {
    ready: false,
    error: null,
    screenJson: null,
    options: null,
    manifest: null,
    hashes: null,
    loadMs: null
  };

  var run = null;   // the last screening result and how it was made
  var card = { borrowerId: null, scenario: null, branch: null, year: null };
  var nextId = 1;

  function byId(id) { return document.getElementById(id); }
  function setHtml(id, html) { var n = byId(id); if (n) { n.innerHTML = html; } }

  // ------------------------------------------------------------------- boot

  function loadScript(url) {
    return new Promise(function (resolve, reject) {
      var tag = document.createElement("script");
      tag.src = url;
      tag.onload = function () { resolve(); };
      tag.onerror = function () { reject(new Error("could not load " + url)); };
      document.head.appendChild(tag);
    });
  }

  function step(line, percent, detail) {
    setHtml("engine-line", esc(line));
    setHtml("engine-detail", esc(detail || ""));
    var bar = byId("engine-bar");
    if (bar) { bar.style.width = percent + "%"; }
  }

  async function sha256Hex(text) {
    if (!window.crypto || !window.crypto.subtle) { return null; }
    var bytes = new TextEncoder().encode(text);
    var digest = await window.crypto.subtle.digest("SHA-256", bytes);
    return Array.prototype.map.call(new Uint8Array(digest), function (b) {
      return b.toString(16).padStart(2, "0");
    }).join("");
  }

  /* Fetch every engine and config file named in the manifest, check it against
   * the hash the manifest states, and write it into Pyodide's filesystem.
   *
   * The hash check is the point of the manifest. The page tells a reader which
   * config produced a number; re-hashing what was actually fetched is what
   * turns that from a claim into a check. crypto.subtle needs a secure context,
   * so over plain http on a non-local host there is no check available, and the
   * page says so rather than implying one happened.
   */
  async function writeBundle(py, manifest) {
    var wanted = manifest.files.filter(function (entry) {
      return entry.kind === "engine" || entry.kind === "config";
    });
    py.FS.mkdirTree(ENGINE_PATH + "/src");
    py.FS.mkdirTree(CONFIG_DIR);

    var texts = await Promise.all(wanted.map(function (entry) {
      return fetch(entry.path).then(function (response) {
        if (!response.ok) {
          throw new Error(entry.path + ": HTTP " + response.status);
        }
        return response.text();
      });
    }));

    var checked = 0;
    var mismatched = [];
    var available = Boolean(window.crypto && window.crypto.subtle);
    for (var i = 0; i < wanted.length; i += 1) {
      if (available) {
        var digest = await sha256Hex(texts[i]);
        if (digest === wanted[i].sha256) {
          checked += 1;
        } else {
          mismatched.push(wanted[i].path);
        }
      }
      py.FS.writeFile(FS_ROOT + "/" + wanted[i].path, texts[i]);
    }

    if (mismatched.length) {
      throw new Error(
        "these files do not match the hashes in cfg/manifest.json, so the page " +
        "will not run them: " + mismatched.join(", ") +
        ". Re-run python -m tools.bundle_webapp."
      );
    }

    var bytes = wanted.reduce(function (total, entry) { return total + entry.bytes; }, 0);
    engine.hashes = {
      files: wanted.length,
      checked: checked,
      available: available,
      bytes: bytes
    };
  }

  async function boot() {
    var started = (window.performance || Date).now();
    try {
      step("Fetching the Python runtime, pinned at Pyodide " + PYODIDE_VERSION, 8,
        "Your data never leaves this page. Only the runtime is downloaded.");
      var manifestWork = fetch("cfg/manifest.json").then(function (response) {
        if (!response.ok) { throw new Error("cfg/manifest.json: HTTP " + response.status); }
        return response.json();
      });
      await loadScript(PYODIDE_BASE + "pyodide.js");

      step("Starting Python in this browser tab", 30,
        "Nothing is sent to a server. The engine runs on your machine.");
      var py = await window.loadPyodide({ indexURL: PYODIDE_BASE });

      step("Loading pyyaml and pydantic", 52,
        "pyyaml reads the rule files, pydantic validates the borrower row.");
      await py.loadPackage(PYODIDE_PACKAGES);

      step("Copying the tested engine and its config into this tab", 74, "");
      var manifest = await manifestWork;
      await writeBundle(py, manifest);

      step("Importing the engine", 90, "");
      py.runPython(
        "import sys\n" +
        "if " + JSON.stringify(ENGINE_PATH) + " not in sys.path:\n" +
        "    sys.path.insert(0, " + JSON.stringify(ENGINE_PATH) + ")\n"
      );
      var api = py.pyimport("src.engine_api");

      engine.screenJson = function (payload) { return api.screen_json(payload); };
      engine.options = JSON.parse(api.form_options_json(CONFIG_DIR));
      engine.manifest = manifest;
      engine.ready = true;
      engine.loadMs = Math.round((window.performance || Date).now() - started);

      buildOptionalFields();
      enrichRequiredFields();
      enable(true);
      renderEngine();
      renderPriceNotice();
      renderProvenance();
    } catch (error) {
      engine.error = error;
      enable(false);
      renderEngine();
    }
  }

  function renderEngine() {
    var panel = byId("engine-panel");
    if (!panel) { return; }
    if (engine.error) {
      panel.className = "panel engine failed";
      setHtml("engine-line", "The engine could not start, so nothing can be screened.");
      setHtml("engine-detail", esc(String(engine.error.message || engine.error)) +
        " The dashboard views on the other tabs still work, because they read the exported file set rather than running the engine.");
      var bar = byId("engine-bar");
      if (bar) { bar.style.width = "100%"; }
      return;
    }
    if (!engine.ready) { return; }
    panel.className = "panel engine ready";
    var hashes = engine.hashes || {};
    setHtml("engine-line",
      "Ready in " + (engine.loadMs / 1000).toFixed(1) + " seconds. " +
      "Pyodide " + esc(PYODIDE_VERSION) + " is running the same Python engine the test suite runs.");
    setHtml("engine-detail",
      hashes.files + " engine and config files loaded, " +
      Number(hashes.bytes || 0).toLocaleString("en-GB") + " bytes. " +
      (hashes.available
        ? hashes.checked + " of " + hashes.files + " matched their sha256 in cfg/manifest.json."
        : "Hashes could not be checked, because this page is not on a secure origin. Serve it over https or from localhost to check them.") +
      " Nothing you enter is uploaded: there is no server call after this point.");
    var bar = byId("engine-bar");
    if (bar) { bar.style.width = "100%"; }
  }

  function enable(on) {
    ["btn-screen", "btn-example", "screen-file"].forEach(function (id) {
      var node = byId(id);
      if (node) { node.disabled = !on; }
    });
    var button = byId("btn-screen");
    if (button && !on) {
      button.textContent = engine.error ? "The engine is not available" : "Waiting for the engine";
    } else if (button) {
      button.textContent = "Screen this borrower";
    }
  }

  // ------------------------------------------------------------- the notices

  function priceStatus() {
    return ((run && run.result.prices) || {}).price_source_status ||
      (engine.manifest ? fixturePriceStatus() : "unknown");
  }

  function fixturePriceStatus() {
    var entry = (engine.manifest.files || []).filter(function (file) {
      return file.path === "cfg/prices_fixture.yaml";
    })[0];
    return entry && entry.meta ? entry.meta.status : "unknown";
  }

  /* The label that goes next to every priced number. thresholds, tiers, flags
   * and questions do not get one, because none of them needs a price. */
  function priceTag() {
    if (priceStatus() === "fixture") {
      return '<span class="chip fixture">fixture prices, not a forecast</span>';
    }
    var label = ((run && run.result.prices) || {}).price_source_label || "the config price set";
    return '<span class="chip status-adopted">prices: ' + esc(label) + "</span>";
  }

  function renderPriceNotice() {
    var node = byId("price-notice");
    if (!node || !engine.manifest) { return; }
    var scenarios = (engine.manifest.files || []).filter(function (file) {
      return file.path === "cfg/scenarios.yaml";
    })[0] || {};
    var fixture = (engine.manifest.files || []).filter(function (file) {
      return file.path === "cfg/prices_fixture.yaml";
    })[0] || {};
    var gated = (scenarios.meta || {}).status !== "adopted" &&
      (scenarios.meta || {}).status !== "scenario";

    if (!gated) {
      node.innerHTML = '<div class="note accent"><p>Carbon prices come from cfg/scenarios.yaml, status ' +
        esc((scenarios.meta || {}).status || "unknown") + ".</p></div>";
      return;
    }
    node.innerHTML = '<div class="note amber"><h3>About the money on this page</h3>' +
      "<p>The carbon prices this tool needs are the NGFS Phase V EU paths, and they could not be " +
      "fetched: the Scenario Explorer is behind a login. So cfg/scenarios.yaml carries the schema, " +
      "the gap and the exact document needed, and no prices at all. Its status says " +
      '<strong>' + esc((scenarios.meta || {}).status || "gated") + "</strong>.</p>" +
      "<p>In its place the page uses <strong>" + esc(fixture.path || "cfg/prices_fixture.yaml") +
      "</strong>, three flat invented values labelled " + priceTag() + ". A real price path rises; " +
      "a flat one cannot be mistaken for a forecast. Every number on this page that depends on a " +
      "price carries that label.</p>" +
      "<p>What does <em>not</em> depend on a price, and is therefore not labelled: what a borrower is, " +
      "whether it crosses the mass threshold of " + esc(thresholdText()) + ", the flags other than " +
      "RATING_GAP, the questions to put to the client, and which fields the tool had to estimate. " +
      "When the prices arrive, the same code reads them from the same file and these labels change " +
      "on their own.</p></div>";
  }

  /* The mass threshold as the config states it, value and unit. Written out of
   * the engine's answer rather than typed into the sentence, because CLAUDE.md
   * section 10 says the page never shows a number that cannot be traced to a
   * config entry, and a number in prose is still a number. */
  function thresholdText() {
    var threshold = (engine.options || {}).mass_threshold;
    if (!threshold) {
      // Before the engine has answered there is no figure to quote, and a
      // placeholder number would be the exact thing this function exists to
      // avoid. Name the file instead.
      return "the value in cbam_rules.yaml";
    }
    return String(threshold.value) + " " + String(threshold.unit || "");
  }

  // --------------------------------------------------------------- the form

  /* The seven required columns from CLAUDE.md section 5. These are input column
   * names, not rules, so they are written out here. Everything that could go
   * stale, the NACE list and the country lists, is filled in from the engine
   * once it has read the config. */
  var REQUIRED = [
    { name: "borrower_id", label: "Borrower reference", type: "text",
      hint: "Your own reference. Filled in for you, change it if you like." },
    { name: "name", label: "Borrower name", type: "text", hint: "As it appears in the credit file." },
    { name: "nace_code", label: "NACE code", type: "text", list: "nace-list",
      hint: "Type or pick one. The list is the sector map in the config in use." },
    { name: "country", label: "Country of the borrower", type: "text", list: "country-list",
      hint: "ISO two-letter code, for example NL." },
    { name: "exposure_eur", label: "Exposure, EUR", type: "number",
      hint: "Your drawn and undrawn exposure. Never used in a cost, only to weight the portfolio." },
    { name: "turnover_eur", label: "Turnover, EUR", type: "number",
      hint: "Used to estimate volumes when you leave an import box empty." },
    { name: "ebitda_eur", label: "EBITDA, EUR", type: "number",
      hint: "May be negative. A non-positive EBITDA has no cost ratio, so the band is set by rule." }
  ];

  function field(spec) {
    var id = "in-" + spec.name;
    var attrs = 'id="' + id + '" name="' + esc(spec.name) + '"';
    if (spec.list) { attrs += ' list="' + esc(spec.list) + '"'; }
    if (spec.type === "number") { attrs += ' inputmode="decimal" step="any"'; }
    if (spec.placeholder) { attrs += ' placeholder="' + esc(spec.placeholder) + '"'; }
    var input = spec.options
      ? '<select ' + attrs + ">" + spec.options.map(function (option) {
          return '<option value="' + esc(option.value) + '">' + esc(option.label) + "</option>";
        }).join("") + "</select>"
      : '<input type="' + (spec.type === "number" ? "number" : "text") + '" ' + attrs + ">";
    return '<div class="field"><label for="' + id + '">' + esc(spec.label) + "</label>" +
      input +
      (spec.hint ? '<span class="hint">' + esc(spec.hint) + "</span>" : "") +
      "</div>";
  }

  function buildRequiredFields() {
    setHtml("required-fields", REQUIRED.map(field).join("") +
      '<datalist id="nace-list"></datalist><datalist id="country-list"></datalist>');
    var reference = byId("in-borrower_id");
    if (reference) { reference.value = newReference(); }
  }

  function newReference() {
    var text = String(nextId);
    while (text.length < 3) { text = "0" + text; }
    nextId += 1;
    return "SCREEN-" + text;
  }

  function enrichRequiredFields() {
    var options = engine.options || {};
    var nace = byId("nace-list");
    if (nace) {
      nace.innerHTML = (options.nace || []).map(function (entry) {
        return '<option value="' + esc(entry.code) + '">' + esc(entry.code) + ", " +
          esc(entry.label) + "</option>";
      }).join("");
    }
    var country = byId("country-list");
    if (country) {
      var rows = (options.eu_countries || []).map(function (code) {
        return '<option value="' + esc(code) + '">' + esc(code) + ", inside the EU customs territory</option>";
      }).concat((options.exempt_countries || []).map(function (entry) {
        return '<option value="' + esc(entry.code) + '">' + esc(entry.code) + ", " +
          esc(entry.name) + ", origin outside CBAM scope</option>";
      }));
      country.innerHTML = rows.join("");
    }
    var hint = byId("in-nace_code");
    if (hint) {
      hint.parentNode.querySelector(".hint").textContent =
        "Type or pick one. The list is the " + (options.nace || []).length +
        " sectors in the config in use. A code that is not in the list is still screened, " +
        "it just gets no sector defaults.";
    }
    var countryHint = byId("in-country");
    if (countryHint && options.eu_countries_source) {
      countryHint.parentNode.querySelector(".hint").textContent =
        "ISO two-letter code. The list is the EU customs territory and the exempt origins " +
        "in the config in use (" + options.eu_countries_source + ").";
    }
  }

  function enumField(name, label, values, hint) {
    return field({
      name: name,
      label: label,
      hint: hint,
      options: [{ value: "", label: "not supplied" }].concat(values.map(function (value) {
        return { value: value, label: value };
      }))
    });
  }

  function buildOptionalFields() {
    var options = engine.options || {};
    var goods = options.goods || [];
    var parts = [];

    parts.push("<h3>What it imports</h3>");
    parts.push('<p class="subtitle">Volumes per year. Tonnes, except electricity, which is MWh. ' +
      "A volume you supply here is the only thing that can put a borrower in the direct-obligation " +
      "group, because a volume estimated from a sector average says nothing about where the goods " +
      "came from. Goods marked below do not count toward the mass threshold of " +
      esc(thresholdText()) + ".</p>");
    parts.push('<div class="form-grid">');
    goods.forEach(function (good) {
      parts.push(field({
        name: good.column,
        label: good.label + (good.column.indexOf("mwh") !== -1 ? ", MWh" : ", tonnes"),
        type: "number",
        hint: good.counts_toward_mass_threshold
          ? "Counts toward the mass threshold."
          : "Does not count toward the mass threshold."
      }));
    });
    parts.push("</div>");

    parts.push("<h3>Where it buys from</h3>");
    parts.push('<div class="form-grid">');
    parts.push(field({
      name: "top_supplier_country", label: "Largest supplier's country", type: "text",
      list: "country-list",
      hint: "An origin left empty is treated as in scope, because a screening tool that " +
        "assumed EU supply would understate the book."
    }));
    parts.push(field({
      name: "share_top_supplier", label: "Share from that supplier, 0 to 1", type: "number",
      hint: "For example 0.72. Needs the country above to be filled in."
    }));
    parts.push("</div>");

    parts.push("<h3>What you know about the relationship</h3>");
    parts.push('<div class="form-grid">');
    var enums = options.enums || {};
    parts.push(enumField("declarant_status", "Authorised CBAM declarant",
      enums.declarant_status || [], "Only an authorised declarant may import these goods."));
    parts.push(enumField("supplier_data", "Supplier emissions data",
      enums.supplier_data || [], "Anything but verified means the config default factor is used."));
    parts.push(enumField("pass_through", "Can it pass the cost on",
      enums.pass_through || [], "Left empty, the sector default share in the config is used instead."));
    parts.push(enumField("bank_transition_rating", "Your own transition rating",
      enums.bank_transition_rating || [],
      "Supplied by you, never computed. Only used to compare against the screening band."));
    parts.push(field({
      name: "working_capital_eur", label: "Working capital, EUR", type: "number",
      hint: "Without it the cash out is shown in euro but not as a share of anything."
    }));
    parts.push("</div>");

    setHtml("optional-fields", parts.join(""));
  }

  function gather() {
    var row = {};
    var form = byId("screen-form");
    if (!form) { return row; }
    Array.prototype.forEach.call(form.querySelectorAll("input[name], select[name]"), function (node) {
      var value = String(node.value || "").trim();
      // An empty box is left out of the row entirely, so the engine sees an
      // absent field and fills it from the sector map, rather than seeing a
      // zero that it would cost as a real volume of nothing.
      if (value !== "") { row[node.name] = value; }
    });
    return row;
  }

  function clearForm() {
    var form = byId("screen-form");
    if (!form) { return; }
    Array.prototype.forEach.call(form.querySelectorAll("input[name], select[name]"), function (node) {
      node.value = "";
    });
    var reference = byId("in-borrower_id");
    if (reference) { reference.value = newReference(); }
    setHtml("form-errors", "");
  }

  /* The CLAUDE.md Task 6 worked example, so a reader can check the tool against
   * a number that is written down in the repo. The 1,000 tonnes and the origin
   * are the inputs; the 3,800 EUR is the engine's answer, not set here. */
  var WORKED_EXAMPLE = {
    name: "Hand Example Steel BV",
    nace_code: "46.72",
    country: "NL",
    exposure_eur: "12000000",
    turnover_eur: "48000000",
    ebitda_eur: "4800000",
    import_t_steel: "1000",
    top_supplier_country: "IN",
    share_top_supplier: "0.72",
    declarant_status: "no",
    supplier_data: "default",
    pass_through: "low",
    bank_transition_rating: "M",
    working_capital_eur: "9000000"
  };

  function fillExample() {
    clearForm();
    Object.keys(WORKED_EXAMPLE).forEach(function (key) {
      var node = byId("in-" + key);
      if (node) { node.value = WORKED_EXAMPLE[key]; }
    });
    var panel = byId("optional-panel");
    if (panel) { panel.open = true; }
  }

  // ---------------------------------------------------------- running it

  function callEngine(rows, label) {
    var payload = JSON.stringify({
      config_dir: CONFIG_DIR,
      prices_path: PRICES_FILE,
      borrowers: rows,
      data_label: label || "UPLOADED"
    });
    var result = JSON.parse(engine.screenJson(payload));
    run = { result: result, at: new Date(), rowCount: rows.length };
    return result;
  }

  function firstBorrowerId(result) {
    var rows = (result.tables && result.tables.dim_borrower) || [];
    return rows.length ? rows[0].borrower_id : null;
  }

  function resetCardState(result) {
    var meta = result.meta || {};
    var years = obligationYears(result);
    card.borrowerId = firstBorrowerId(result);
    card.scenario = meta.default_scenario || null;
    card.branch = meta.default_branch || null;
    // The first obligation year the config defines, which is where an analyst
    // starts: it is the first year an obligation exists at all.
    card.year = years.length ? years[0] : "";
  }

  function submitForm(event) {
    if (event) { event.preventDefault(); }
    if (!engine.ready) { return; }
    var row = gather();
    var missing = REQUIRED.filter(function (spec) { return !row[spec.name]; });
    if (missing.length) {
      setHtml("form-errors", '<div class="note amber"><p>These are required and were left empty: ' +
        esc(missing.map(function (spec) { return spec.label; }).join(", ")) +
        ". The engine refuses a row that is missing one of the seven, rather than filling it in.</p></div>");
      return;
    }
    setHtml("form-errors", "");
    var result = callEngine([row], "UPLOADED");
    setHtml("screen-batch", "");
    resetCardState(result);
    renderResult();
    var anchor = byId("screen-result");
    if (anchor) { anchor.scrollIntoView({ block: "start" }); }
  }

  // What the batch table was last drawn from, so a change of scenario, branch
  // or year can redraw it without re-reading the file or re-running the engine.
  var batch = { fileName: "", rowsRead: 0 };

  function handleCsv(file) {
    if (!engine.ready || !file) { return; }
    batch.fileName = file.name;
    file.text().then(function (text) {
      var rows = (U.parseCsv || function () { return []; })(stripComments(text));
      if (!rows.length) {
        setHtml("screen-batch", '<div class="note amber"><p>That file had no rows once the header ' +
          "and any comment lines were read.</p></div>");
        return;
      }
      var result = callEngine(rows, "UPLOADED");
      resetCardState(result);
      batch.rowsRead = rows.length;
      renderResult();
      renderBatch();
    });
  }

  /* The template and the fixture borrower files carry a leading comment line so
   * that a number can never travel without its label. The engine's own csv
   * reader drops those; this one has to as well. */
  function stripComments(text) {
    return text.split(/\r?\n/).filter(function (line) {
      return line.trim().charAt(0) !== "#";
    }).join("\n");
  }

  // -------------------------------------------------------- reading a result

  function obligationYears(result) {
    return ((result.tables || {}).dim_year || [])
      .filter(function (row) { return row.is_obligation_year === "true"; })
      .map(function (row) { return row.year; });
  }

  function scenarioRows(result) {
    return ((result.tables || {}).dim_scenario || []).slice().sort(function (a, b) {
      return num(a.sort_order) - num(b.sort_order);
    });
  }

  function branchRows(result) {
    return ((result.tables || {}).dim_branch || []).slice().sort(function (a, b) {
      return num(a.sort_order) - num(b.sort_order);
    });
  }

  function labelOf(rows, key, keyColumn, labelColumn) {
    var found = rows.filter(function (row) { return row[keyColumn] === key; })[0];
    return found ? found[labelColumn] : key;
  }

  function borrowerRow(result, id) {
    return ((result.tables || {}).dim_borrower || []).filter(function (row) {
      return row.borrower_id === id;
    })[0] || null;
  }

  function detailOf(result, id) {
    return (result.borrowers || []).filter(function (row) {
      return row.borrower_id === id;
    })[0] || null;
  }

  function costRow(result, id, year, scenario, branch) {
    return ((result.tables || {}).fact_cost || []).filter(function (row) {
      return row.borrower_id === id && row.year === year &&
        row.scenario === scenario && row.branch === branch;
    })[0] || null;
  }

  function costLines(result, id, year, scenario, branch) {
    return ((result.tables || {}).fact_cost_line || []).filter(function (row) {
      return row.borrower_id === id && row.year === year &&
        row.scenario === scenario && row.branch === branch;
    }).sort(function (a, b) { return num(a.line_order) - num(b.line_order); });
  }

  function activeFlags(result, id) {
    return ((result.tables || {}).fact_flags || []).filter(function (row) {
      return row.borrower_id === id && row.active === "true";
    }).sort(function (a, b) { return a.flag.localeCompare(b.flag); });
  }

  function withheldFlags(result) {
    var names = String((result.meta || {}).withheld_flags || "").split(", ").filter(Boolean);
    return names;
  }

  function questionsFor(result, flagCode) {
    return ((result.tables || {}).questions || []).filter(function (row) {
      return row.flag === flagCode;
    }).sort(function (a, b) { return num(a.question_order) - num(b.question_order); });
  }

  function shockYear(result) {
    return String((result.meta || {}).liquidity_shock_year || "");
  }

  function liquidityRows(result, id, scenario, branch, year) {
    return ((result.tables || {}).fact_liquidity || []).filter(function (row) {
      return row.borrower_id === id && row.scenario === scenario &&
        row.branch === branch && row.year === year;
    }).sort(function (a, b) {
      return String(a.quarter || "0").localeCompare(String(b.quarter || "0"));
    });
  }

  // ---------------------------------------------------------- drawing it

  function cardTile(key, value, note, flagged) {
    return '<div class="card' + (flagged ? " flagged" : "") + '"><span class="k">' + esc(key) +
      '</span><span class="v">' + value + '</span><span class="n">' + note + "</span></div>";
  }

  function renderResult() {
    var node = byId("screen-result");
    if (!node || !run) { return; }
    var result = run.result;

    if (!result.ok) {
      node.innerHTML = '<div class="panel"><h2>Nothing could be screened</h2>' +
        (result.error ? "<p>" + esc(result.error) + "</p>" : "") +
        rejectedList(result) + "</div>";
      return;
    }
    if (!card.borrowerId) {
      node.innerHTML = '<div class="panel"><h2>No borrower came back</h2>' + rejectedList(result) + "</div>";
      return;
    }
    node.innerHTML = renderCard(result, card.borrowerId);
  }

  function rejectedList(result) {
    var rejected = result.rejected || [];
    if (!rejected.length) { return ""; }
    return '<div class="note amber"><p><strong>' + rejected.length +
      (rejected.length === 1 ? " row was rejected" : " rows were rejected") +
      " by the engine.</strong> A rejected row is not screened at all, rather than screened with " +
      "a guess in place of what was wrong.</p><ul>" +
      rejected.map(function (message) { return "<li>" + esc(message) + "</li>"; }).join("") +
      "</ul></div>";
  }

  function renderCard(result, id) {
    var borrower = borrowerRow(result, id);
    var detail = detailOf(result, id);
    if (!borrower || !detail) { return ""; }
    var priced = Boolean(result.priced);
    var row = priced ? costRow(result, id, card.year, card.scenario, card.branch) : null;
    var tier = (detail.tier_by_branch || {})[card.branch] ||
      (detail.tier_by_branch || {})[(result.meta || {}).default_branch] || {};

    var html = [];
    html.push('<div class="panel result">');
    html.push("<h2>" + esc(borrower.name) + "</h2>");
    html.push('<p class="subtitle">' + esc(borrower.borrower_id) + ", NACE " +
      esc(borrower.nace_code) + ", " + esc(borrower.country) +
      ". Screened in this browser tab at " + esc(run.at.toLocaleTimeString("en-GB")) +
      " by the Python engine, not by this page.</p>");

    html.push(cardControls(result));

    html.push('<div class="cards">');
    html.push(cardTile("What it is", esc(tier.label || ""),
      esc(tier.reason || "") + " No price is involved in this decision."));

    if (priced && row) {
      html.push(cardTile("Certificate cost, " + esc(card.year),
        esc(U.eur(num(row.cost_eur))) + " " + priceTag(),
        esc(row.cost_basis_label) + "."));
      html.push(cardTile("Cost against EBITDA",
        (row.materiality_ratio ? esc(U.pct(num(row.materiality_ratio))) : "no ratio") + " " +
        U.bandChip(row.band, row.band_label) + " " + priceTag(),
        row.materiality_ratio
          ? "Band " + esc(row.band) + ", " + esc(row.band_label) +
            ". The band edges are the author's assumptions in thresholds.yaml, not law."
          : "EBITDA is " + esc(U.eur(num(borrower.ebitda_eur))) +
            ", so the ratio has no meaning and the band was set by the rule in thresholds.yaml."));
      var cash = U.sum(liquidityRows(result, id, card.scenario, card.branch, shockYear(result)),
        function (flow) { return num(flow.cash_out_eur); });
      html.push(cardTile("Cash out in " + esc(shockYear(result)),
        esc(U.eur(cash)) + " " + priceTag(),
        borrower.working_capital_eur
          ? "Against working capital of " + esc(U.eur(num(borrower.working_capital_eur))) + "."
          : "No working capital was supplied, so no share of it is shown rather than a guessed one."));
    } else {
      html.push(cardTile("Certificate cost", "not computed",
        "No carbon price set was available, so no cost, band or cash flow is reported. " +
        "A zero here would have been a number about the world, and there is none.", true));
    }

    html.push(cardTile("Mass threshold",
      esc(U.plain(detail.counted_tonnes)) + " t of " + esc(U.plain(detail.threshold_tonnes)) + " t",
      detail.below_threshold
        ? "Below it, so nothing is owed this year whatever the tonnage is worth. No price is involved in this test."
        : "Above it, so the whole year's imports carry an obligation. No price is involved in this test."));
    html.push("</div>");

    html.push(flagChips(result, id));
    html.push(estimatedNotice(detail));

    if (priced) {
      html.push(costPathChart(result, id, borrower));
      html.push(cashOutChart(result, id, borrower));
    } else {
      html.push('<div class="note amber"><p>The cost path chart, the materiality band and the ' +
        "2027 cash out are not drawn, because they cannot be computed without a carbon price. " +
        esc((result.meta || {}).unpriced_note || "") + "</p></div>");
    }

    html.push(whyThisRating(result, id, borrower, detail, row, tier));
    html.push(whatToAsk(result, id));
    html.push(addToDataset(result));
    html.push("</div>");
    return html.join("");
  }

  function cardControls(result) {
    var scenarios = scenarioRows(result);
    var branches = branchRows(result);
    var years = obligationYears(result);
    if (!result.priced) {
      return '<div class="controls no-print"><div class="field"><label for="card-branch">Rule branch</label>' +
        '<select id="card-branch">' + branches.map(function (row) {
          return '<option value="' + esc(row.branch) + '"' +
            (row.branch === card.branch ? " selected" : "") + ">" +
            esc(row.branch_label) + " (" + esc(row.status) + ")</option>";
        }).join("") + "</select>" +
        '<span class="hint">Which rule set applies. No price is involved.</span></div></div>';
    }
    return '<div class="controls no-print">' +
      '<div class="field"><label for="card-scenario">Carbon price scenario</label><select id="card-scenario">' +
      scenarios.map(function (row) {
        return '<option value="' + esc(row.scenario) + '"' +
          (row.scenario === card.scenario ? " selected" : "") + ">" + esc(row.scenario_label) + "</option>";
      }).join("") + "</select>" +
      '<span class="hint">' + (priceStatus() === "fixture"
        ? "Fixture prices, not a forecast." : "From the config price set.") + "</span></div>" +
      '<div class="field"><label for="card-branch">Rule branch</label><select id="card-branch">' +
      branches.map(function (row) {
        return '<option value="' + esc(row.branch) + '"' +
          (row.branch === card.branch ? " selected" : "") + ">" +
          esc(row.branch_label) + " (" + esc(row.status) + ")</option>";
      }).join("") + "</select>" +
      '<span class="hint">The proposal branch applies Commission proposals that are not law.</span></div>' +
      '<div class="field"><label for="card-year">Obligation year</label><select id="card-year">' +
      years.map(function (year) {
        return '<option value="' + esc(year) + '"' + (year === card.year ? " selected" : "") +
          ">" + esc(year) + "</option>";
      }).join("") + "</select>" +
      '<span class="hint">Which year the cost card above describes.</span></div></div>';
  }

  function flagChips(result, id) {
    var flags = activeFlags(result, id);
    var withheld = result.priced ? [] : withheldFlags(result);
    var html = ['<div class="note"><p><strong>Flags.</strong> '];
    if (!flags.length) {
      html.push("None is active for this borrower.");
    } else {
      html.push(flags.map(function (flag) {
        return '<span class="chip flag">' + esc(flag.flag) + "</span>";
      }).join(" "));
    }
    html.push("</p>");
    if (withheld.length) {
      html.push("<p>Not evaluated, because " +
        (withheld.length === 1 ? "it needs" : "they need") + " a carbon price and there is none: " +
        withheld.map(function (code) {
          return '<span class="chip">' + esc(code) + "</span>";
        }).join(" ") + "</p>");
    }
    html.push("<p>Each flag's reason and the questions it raises are in the two boxes below.</p></div>");
    return html.join("");
  }

  function estimatedNotice(detail) {
    if (!detail.estimated && !(detail.unfilled_fields || []).length) { return ""; }
    var html = ['<div class="note amber"><h3>What the tool had to fill in</h3>'];
    if (detail.estimated) {
      html.push("<p><strong>Estimated from sector averages:</strong> " +
        esc((detail.estimated_fields || []).join(", ")) +
        ". Those are averages for the sector, not facts about this borrower, so the cost is " +
        "indicative only and the client should be asked to confirm the volumes.</p>");
      if (Object.keys(detail.supplied_quantities || {}).length === 0 &&
          Object.keys(detail.quantities || {}).length > 0) {
        html.push("<p>The import volume above was estimated, so it does <strong>not</strong> put this " +
          "borrower in the direct-obligation group. An average for a sector says nothing about where " +
          "one borrower's goods came from, and a tool that treated it as though it did would make a " +
          "customs declarant out of everybody.</p>");
      }
    }
    if ((detail.unfilled_fields || []).length) {
      html.push("<p><strong>Left empty, because the config has no default for them:</strong> " +
        esc(detail.unfilled_fields.join(", ")) +
        ". A field with no default stays empty and is named here, rather than becoming a zero.</p>");
    }
    html.push("</div>");
    return html.join("");
  }

  function costPathChart(result, id, borrower) {
    var years = obligationYears(result);
    var series = scenarioRows(result).map(function (scenario) {
      return {
        label: scenario.scenario_label,
        values: years.map(function (year) {
          var row = costRow(result, id, year, scenario.scenario, card.branch);
          return row ? (num(row.cost_eur) || 0) : 0;
        })
      };
    });
    return '<div class="panel"><h2>Certificate cost path</h2>' +
      '<p class="subtitle">' + esc(years[0] + " to " + years[years.length - 1]) +
      ", every carbon-price scenario in the config, on " +
      esc(labelOf(branchRows(result), card.branch, "branch", "branch_label")) +
      ". Every point is a number the engine returned. " + priceTag() + "</p>" +
      U.lineChart(series, {
        xs: years,
        ariaLabel: "Certificate cost by year for " + borrower.name +
          " under each carbon price scenario, in fixture prices"
      }) + "</div>";
  }

  function cashOutChart(result, id, borrower) {
    var year = shockYear(result);
    var flows = liquidityRows(result, id, card.scenario, card.branch, year);
    var html = ['<div class="panel"><h2>Cash out in ' + esc(year) + "</h2>"];
    if (!flows.length) {
      html.push('<p class="subtitle">This borrower has no payment rows in ' + esc(year) +
        " for this scenario and branch. A borrower that owes nothing has no payment rows at all, " +
        "rather than a row of zeros.</p></div>");
      return html.join("");
    }
    html.push('<p class="subtitle">Certificates cannot be bought before 1 February ' + esc(year) +
      ", so the whole " + esc(String(Number(year) - 1)) + " obligation lands as one block, while the " +
      esc(year) + " obligation already prepays in the same year. Two obligations in one year is the shock. " +
      priceTag() + "</p>");
    html.push(U.barChart(flows.map(function (flow) {
      return {
        label: flow.window + ", " + flow.kind,
        short: flow.window.replace(year + " ", ""),
        value: num(flow.cash_out_eur) || 0,
        colour: flow.kind === "block" ? "#f9bd20" : "#22c7b6"
      };
    }), { ariaLabel: "Cash out by payment window in " + year + " for " + borrower.name }));
    var total = U.sum(flows, function (flow) { return num(flow.cash_out_eur); });
    // Each payment's share of working capital is a number the engine returned.
    // The year's share is their sum, not a division done here.
    var share = flows[0].share_of_working_capital
      ? U.sum(flows, function (flow) { return num(flow.share_of_working_capital); })
      : null;
    html.push('<p class="subtitle">Total ' + esc(U.eur(total)) + (share !== null
      ? ", which is " + esc(U.pct(share, 1)) +
        " of the working capital supplied. Per payment: " +
        flows.map(function (flow) {
          return esc(flow.window) + " " + esc(U.pct(num(flow.share_of_working_capital), 1));
        }).join(", ") + "."
      : ". No working capital was supplied, so no share of it is shown.") + "</p></div>");
    return html.join("");
  }

  function whyThisRating(result, id, borrower, detail, row, tier) {
    var html = ['<div class="panel"><h2>Why this rating</h2>'];
    html.push('<p class="subtitle">The engine\'s own formula with this borrower\'s own numbers. ' +
      "Every value below is a field the engine returned, multiplied out so it can be checked by hand.</p>");

    html.push('<div class="note"><p><strong>What it is.</strong> ' + esc(tier.label || "") + ". " +
      esc(tier.reason || "") + "</p>");
    html.push("<p><strong>Mass threshold.</strong> " + esc(U.plain(detail.counted_tonnes)) +
      " t of counting goods against a " + esc(U.plain(detail.threshold_tonnes)) + " t threshold, so this borrower is " +
      (detail.below_threshold ? "below it and owes nothing this year" : "above it") + ".</p>");
    if (tier.origin_unknown) {
      html.push("<p><strong>Origin.</strong> No supplier country was given, so the tool could not test " +
        "whether the origin is exempt and treated it as in scope. Confirm the origin before acting on this.</p>");
    }
    html.push("</div>");

    if (!result.priced) {
      html.push('<div class="note amber"><p>There is no cost line to multiply out, because there is ' +
        "no carbon price. " + esc((result.meta || {}).unpriced_note || "") + "</p></div></div>");
      return html.join("");
    }
    if (!row) {
      html.push('<div class="note amber"><p>No cost row for this year, scenario and branch.</p></div></div>');
      return html.join("");
    }

    var lines = costLines(result, id, card.year, card.scenario, card.branch);
    if (!lines.length) {
      html.push('<div class="note amber"><p>' + esc(row.cost_basis_label) +
        ". There is no cost line to multiply out, so the cost shown is " +
        esc(U.eur(num(row.cost_eur))) + ".</p></div>");
    } else {
      var body = [];
      lines.forEach(function (line) {
        var multiplier = line.cost_basis === "lost_allocation"
          ? U.plain(num(line.charged_share)) + "  (" + line.charged_share_label + ")"
          : "(1 - " + U.plain(num(line.free_allocation_share)) + ")";
        body.push(line.good_group + ":  " +
          U.plain(num(line.quantity)) + " t  x  " +
          U.plain(num(line.emission_factor)) + " tCO2 per t  x  EUR " +
          U.plain(num(line.price_eur)) + "  x  " + multiplier +
          "  =  " + U.eur2(num(line.cost_eur)) +
          "\n    emission factor basis: " + line.factor_basis);
      });
      body.push("");
      body.push("total cost  =  " + U.eur2(num(row.cost_eur)));
      if (row.materiality_ratio) {
        body.push("cost / EBITDA  =  " + U.eur2(num(row.cost_eur)) + " / " +
          U.eur2(num(borrower.ebitda_eur)) + "  =  " + U.pct(num(row.materiality_ratio)) +
          "  ->  band " + row.band + ", " + row.band_label);
      } else {
        body.push("EBITDA is " + U.eur2(num(borrower.ebitda_eur)) +
          ", so there is no meaningful ratio and the band was set by the rule in config: " +
          row.band + ", " + row.band_label);
      }
      html.push('<pre class="formula">' + esc(body.join("\n")) + "</pre>");
      html.push('<p class="subtitle">' + priceTag() + " The carbon price in that line is " +
        esc(U.eur2(num(row.price_eur))) + " per tonne, from " +
        esc((result.prices || {}).price_source || "the price file in the bundle") +
        ". The free allocation still granted in " + esc(card.year) + " is " +
        esc(U.pct(num(row.free_allocation_share), 1)) + ", from cbam_rules.yaml.</p>");
    }
    html.push("</div>");
    return html.join("");
  }

  function whatToAsk(result, id) {
    var flags = activeFlags(result, id);
    var html = ['<div class="panel"><h2>What to ask this client</h2>'];
    if (!flags.length) {
      html.push("<p>No flag is active for this borrower, so the tool has nothing to ask.</p></div>");
      return html.join("");
    }
    html.push('<p class="subtitle">One or two questions per flag, from questions.yaml. The wording is ' +
      "the author's, not law and not supervisory guidance.</p>");
    html.push('<dl class="qa">');
    flags.forEach(function (flag) {
      html.push("<dt>" + esc(flag.flag) + "</dt><dd>");
      html.push('<div class="reason">' + esc(flag.reason) + "</div>");
      var questions = questionsFor(result, flag.flag);
      if (questions.length) {
        html.push("<ul>" + questions.map(function (question) {
          return "<li>" + esc(question.question_text) + "</li>";
        }).join("") + "</ul>");
      } else {
        html.push('<div class="reason">questions.yaml has nothing for this flag.</div>');
      }
      html.push("</dd>");
    });
    html.push("</dl></div>");
    return html.join("");
  }

  function addToDataset(result) {
    var count = ((result.tables || {}).dim_borrower || []).length;
    return '<div class="panel"><h2>Put ' + (count === 1 ? "this borrower" : "these " + count + " borrowers") +
      " on the dashboard</h2>" +
      '<p class="subtitle">The engine returns the same tables the Power BI exporter writes, so a ' +
      "screened borrower drops straight into the Portfolio, Borrowers and Summary views alongside the " +
      "file set this page opened with. Screened rows are marked SCREENED in the Source column so the " +
      "two are never confused.</p>" +
      '<div class="controls no-print"><button class="action primary" type="button" id="btn-add">' +
      'Add to the dashboard</button></div><div id="add-report"></div></div>';
  }

  // ------------------------------------------------------------ the batch

  function renderBatch() {
    var node = byId("screen-batch");
    if (!node || !run) { return; }
    var result = run.result;
    var borrowers = (result.tables || {}).dim_borrower || [];
    var html = [];
    html.push('<div class="note accent"><p><strong>' + esc(batch.fileName) + "</strong>: " +
      batch.rowsRead + (batch.rowsRead === 1 ? " row read" : " rows read") + ", " + result.accepted +
      " accepted by the engine, " + (result.rejected || []).length + " rejected. " +
      "The file was read in this tab and never uploaded.</p></div>");
    html.push(rejectedList(result));

    if (borrowers.length) {
      html.push('<p class="subtitle">Select a name to see its card below. Cost and band are shown for ' +
        esc(card.year) + ", " + esc(labelOf(scenarioRows(result), card.scenario, "scenario", "scenario_label")) +
        ", " + esc(labelOf(branchRows(result), card.branch, "branch", "branch_label")) +
        (result.priced ? ". " + priceTag() : "") + "</p>");
      html.push('<div class="scroll-x"><table><thead><tr><th scope="col" class="wrap">Borrower</th>' +
        '<th scope="col" class="wrap">What it is</th><th scope="col">Band</th>' +
        '<th scope="col" class="num">Cost</th><th scope="col" class="num">Counted t</th>' +
        '<th scope="col" class="wrap">Flags</th><th scope="col">Estimated</th></tr></thead><tbody>');
      borrowers.forEach(function (borrower) {
        var id = borrower.borrower_id;
        var row = result.priced ? costRow(result, id, card.year, card.scenario, card.branch) : null;
        var detail = detailOf(result, id) || {};
        var flags = activeFlags(result, id);
        html.push("<tr>" +
          '<th scope="row" class="wrap"><button type="button" class="linklike pick-screened" data-id="' +
          esc(id) + '">' + esc(borrower.name) + "</button></th>" +
          '<td class="wrap">' + esc(row ? row.tier_label_plain : borrower.tier_label_plain) + "</td>" +
          "<td>" + (row ? U.bandChip(row.band, row.band_label) : "not priced") + "</td>" +
          '<td class="num">' + esc(row ? U.eur(num(row.cost_eur)) : "not computed") + "</td>" +
          '<td class="num">' + esc(U.plain(detail.counted_tonnes)) + "</td>" +
          '<td class="wrap">' + (flags.length
            ? flags.map(function (flag) { return '<span class="chip flag">' + esc(flag.flag) + "</span>"; }).join(" ")
            : '<span class="chip">none</span>') + "</td>" +
          "<td>" + (detail.estimated ? '<span class="chip est">estimated</span>' : "") + "</td></tr>");
      });
      html.push("</tbody></table></div>");
    }
    node.innerHTML = html.join("");
  }

  // ------------------------------------------------------------ provenance

  function renderProvenance() {
    var node = byId("config-provenance");
    if (!node || !engine.manifest) { return; }
    var manifest = engine.manifest;
    var html = [];
    html.push("<p>These are the files the engine loaded into this tab, hashed when they were bundled " +
      "and re-hashed here after they were fetched. A number on this page came out of these bytes " +
      "and no others.</p>");
    html.push('<div class="note"><p>' + esc(manifest.rule_config_note || "") + "</p><p>" +
      esc(manifest.price_note || "") + "</p></div>");
    html.push('<div class="scroll-x"><table><thead><tr><th scope="col">File</th>' +
      '<th scope="col" class="wrap">Bundled from</th><th scope="col">Status</th>' +
      '<th scope="col">Version</th><th scope="col" class="num">Bytes</th>' +
      '<th scope="col" class="wrap">sha256</th></tr></thead><tbody>');
    (manifest.files || []).forEach(function (entry) {
      var meta = entry.meta || {};
      html.push("<tr><td>" + esc(entry.path) + "</td>" +
        '<td class="wrap">' + esc(entry.source) + "</td>" +
        "<td>" + (meta.status
          ? '<span class="chip ' + (meta.status === "adopted" ? "status-adopted" : "status-proposal") +
            '">' + esc(meta.status) + "</span>"
          : "") + "</td>" +
        "<td>" + esc(meta.config_version || "") + "</td>" +
        '<td class="num">' + Number(entry.bytes).toLocaleString("en-GB") + "</td>" +
        '<td class="wrap mono tiny">' + esc(entry.sha256) + "</td></tr>");
    });
    html.push("</tbody></table></div>");
    html.push('<p class="subtitle">Bundled ' + esc(manifest.generated_at || "at an unrecorded time") +
      " by " + esc(manifest.generated_by || "") + ". Entrypoint: " +
      esc(manifest.engine_entrypoint || "") + ", the same function tests/test_engine_api.py calls.</p>");
    node.innerHTML = html.join("");
  }

  // ---------------------------------------------------------------- events

  function wire() {
    var form = byId("screen-form");
    if (form) { form.addEventListener("submit", submitForm); }
    var example = byId("btn-example");
    if (example) { example.addEventListener("click", fillExample); }
    var clear = byId("btn-clear-form");
    if (clear) { clear.addEventListener("click", clearForm); }
    var file = byId("screen-file");
    if (file) {
      file.addEventListener("change", function (event) {
        if (event.target.files && event.target.files[0]) { handleCsv(event.target.files[0]); }
      });
    }

    document.addEventListener("change", function (event) {
      var id = event.target.id;
      if (id !== "card-scenario" && id !== "card-branch" && id !== "card-year") { return; }
      if (id === "card-scenario") { card.scenario = event.target.value; }
      if (id === "card-branch") { card.branch = event.target.value; }
      if (id === "card-year") { card.year = event.target.value; }
      renderResult();
      if (batch.rowsRead) { renderBatch(); }
    });

    document.addEventListener("click", function (event) {
      var pick = event.target.closest ? event.target.closest(".pick-screened") : null;
      if (pick) {
        card.borrowerId = pick.dataset.id;
        renderResult();
        var anchor = byId("screen-result");
        if (anchor) { anchor.scrollIntoView({ block: "start" }); }
        return;
      }
      var add = event.target.closest ? event.target.closest("#btn-add") : null;
      if (add && run) {
        var outcome = (U.addScreened || function () {
          return { ok: false, why: "The dashboard is not available on this page." };
        })(run.result);
        setHtml("add-report", outcome.ok
          ? '<div class="note accent"><p>' + outcome.added +
            (outcome.added === 1 ? " borrower is" : " borrowers are") +
            " now on the Portfolio, Borrowers and Summary views, marked SCREENED in the Source column. " +
            "Nothing was uploaded.</p></div>"
          : '<div class="note amber"><p>' + esc(outcome.why) + "</p></div>");
      }
    });
  }

  function start() {
    buildRequiredFields();
    enable(false);
    wire();
    boot();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
}());
