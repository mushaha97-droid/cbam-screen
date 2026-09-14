"""Structural checks on the hand-authored Power BI project.

Power BI Desktop is the only thing that can truly validate a PBIP, and it is a
GUI, so it cannot run here. What can run here is everything short of that: the
json files parse and carry the keys their published schemas mark required, the
TMDL files are internally consistent, and, most usefully, the model's columns
and the exporter's columns still match each other.

That last check is the one that earns its keep. A column renamed in
src/export_powerbi.py and not renamed in the TMDL would show up in Desktop as a
refresh error long after the change, and only if someone opened the file. Here
it fails in a second.

These tests do not prove the model opens. Nothing outside Desktop can. See the
"What is checked and what is not" section of docs/POWERBI.md.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
POWERBI = REPO_ROOT / "powerbi"
MODEL = POWERBI / "cbam-screen.SemanticModel"
DEFINITION = MODEL / "definition"
TABLES = DEFINITION / "tables"
REPORT = POWERBI / "cbam-screen.Report"
THEME = POWERBI / "theme" / "cbam-theme.json"
EXPORT = REPO_ROOT / "data" / "powerbi_fixture" / "export"

# Tables in the model that are loaded from a csv. _Measures is not: it holds
# only measures and a hidden placeholder column.
CSV_BACKED = (
    "dim_borrower",
    "dim_scenario",
    "dim_branch",
    "dim_year",
    "fact_cost",
    "fact_liquidity",
    "fact_flags",
    "meta",
)

HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


def json_files() -> list[Path]:
    """Every file in powerbi/ whose content is json, whatever its extension."""
    found = [POWERBI / "cbam-screen.pbip", THEME]
    for folder in (MODEL, REPORT):
        found.append(folder / ".platform")
        found.extend(sorted(folder.rglob("*.json")))
    found.append(MODEL / "definition.pbism")
    found.append(REPORT / "definition.pbir")
    return [path for path in found if path.is_file()]


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def table_text(name: str) -> str:
    return (TABLES / f"{name}.tmdl").read_text(encoding="utf-8")


def tmdl_files() -> list[Path]:
    return sorted(DEFINITION.rglob("*.tmdl"))


# ---------------------------------------------------------------------------
# json: everything parses and carries its required keys
# ---------------------------------------------------------------------------


def test_every_json_file_parses() -> None:
    paths = json_files()
    assert len(paths) >= 9
    for path in paths:
        read_json(path)  # raises on malformed json


def test_the_pbip_pointer_matches_its_published_schema() -> None:
    data = read_json(POWERBI / "cbam-screen.pbip")
    assert set(data) <= {"$schema", "version", "artifacts", "settings"}
    for key in ("$schema", "version", "artifacts"):
        assert key in data
    assert data["$schema"].startswith(
        "https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1."
    )
    # The published schema allows exactly one artifact member, report, and it is
    # required. There is no semantic-model artifact type, which is why this
    # project ships a report folder at all.
    assert len(data["artifacts"]) == 1
    assert list(data["artifacts"][0]) == ["report"]
    pointed = POWERBI / data["artifacts"][0]["report"]["path"]
    assert pointed.is_dir(), f"the pbip points at {pointed}, which does not exist"


def test_the_semantic_model_definition_allows_tmdl() -> None:
    data = read_json(MODEL / "definition.pbism")
    assert "$schema" in data and "version" in data
    # Version 1.0 means TMSL only. TMDL needs 4.0 or above.
    assert float(data["version"]) >= 4.0
    assert DEFINITION.is_dir()


def test_the_report_points_back_at_the_semantic_model_by_path() -> None:
    data = read_json(REPORT / "definition.pbir")
    target = (REPORT / data["datasetReference"]["byPath"]["path"]).resolve()
    assert target == MODEL.resolve()


def test_platform_files_declare_the_right_item_types() -> None:
    assert read_json(MODEL / ".platform")["metadata"]["type"] == "SemanticModel"
    assert read_json(REPORT / ".platform")["metadata"]["type"] == "Report"
    ids = {
        read_json(MODEL / ".platform")["config"]["logicalId"],
        read_json(REPORT / ".platform")["config"]["logicalId"],
    }
    assert len(ids) == 2, "two items must not share a logicalId"


def test_the_report_pages_are_declared_consistently() -> None:
    pages = read_json(REPORT / "definition" / "pages" / "pages.json")
    for name in pages["pageOrder"]:
        page = read_json(REPORT / "definition" / "pages" / name / "page.json")
        for key in ("$schema", "name", "displayName", "displayOption"):
            assert key in page
        assert page["name"] == name, "the folder name must match the page name"
        assert len(page["name"]) <= 50
        assert re.fullmatch(r"[\w-]+", page["name"])
        if page["displayOption"] != "DeprecatedDynamic":
            assert "height" in page and "width" in page
    assert pages["activePageName"] in pages["pageOrder"]


def test_the_report_ships_no_hand_written_visuals() -> None:
    """Deliberate. See docs/POWERBI.md.

    The PBIR container schemas are published, but the visualType strings and the
    query payloads inside a visual are not, so a hand-written visual would be a
    guess. The canvas is empty and the visuals are specified in prose instead.
    """
    assert not list(REPORT.rglob("visual.json"))


# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------


def test_the_theme_is_valid_json_with_one_canvas_commitment() -> None:
    theme = read_json(THEME)
    assert theme["name"]
    # One canvas commitment, and it is light. Everything else follows from it.
    assert theme["background"] == "#FFFFFF"
    assert len(theme["dataColors"]) >= 8
    for key in (
        "dataColors",
        "good",
        "neutral",
        "bad",
        "minimum",
        "center",
        "maximum",
        "firstLevelElements",
        "tableAccent",
    ):
        assert key in theme, f"the theme has no {key}"


def test_every_theme_colour_is_a_six_digit_hex() -> None:
    theme = read_json(THEME)
    for key, value in theme.items():
        if isinstance(value, str) and value.startswith("#"):
            assert HEX.fullmatch(value), f"{key} is {value}"
    for colour in theme["dataColors"]:
        assert HEX.fullmatch(colour), colour


def test_the_data_colours_are_distinguishable_by_lightness_as_well_as_hue() -> None:
    """Colour alone must not be the only channel carrying meaning.

    Not a contrast check, which needs a proper colour model. This is the weaker
    but still useful claim: the first four series colours, which are the ones a
    small multiple chart will actually use, do not all sit at the same
    perceived lightness, so a reader with deuteranopia has something else to go
    on. The real defence is the band ramp, which is ordered by lightness alone.
    """
    theme = read_json(THEME)

    def luminance(colour: str) -> float:
        red, green, blue = (int(colour[i : i + 2], 16) for i in (1, 3, 5))
        return 0.2126 * red + 0.7152 * green + 0.0722 * blue

    first_four = sorted(luminance(c) for c in theme["dataColors"][:4])
    assert first_four[-1] - first_four[0] > 40

    # The conditional-formatting ramp must run light to dark, so a band heatmap
    # reads correctly in greyscale and to a colourblind reader.
    ramp = [theme["minimum"], theme["center"], theme["maximum"]]
    values = [luminance(colour) for colour in ramp]
    assert values[0] > values[1] > values[2]


# ---------------------------------------------------------------------------
# TMDL structure
# ---------------------------------------------------------------------------


def test_tmdl_files_indent_with_tabs() -> None:
    """TMDL's indentation rule is a single tab per level.

    Inner alignment inside an M or DAX block may use spaces after the tabs,
    which is what Desktop itself emits, so the rule checked here is that any
    indented line starts with a tab.
    """
    for path in tmdl_files():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line and line[0] == " ":
                raise AssertionError(f"{path.name} line {number} indents with a space")


def test_every_referenced_table_has_a_file_and_every_file_is_referenced() -> None:
    model = (DEFINITION / "model.tmdl").read_text(encoding="utf-8")
    referenced = set(re.findall(r"^ref table (.+)$", model, re.MULTILINE))
    on_disk = {path.stem for path in TABLES.glob("*.tmdl")}
    assert referenced == on_disk


def test_every_table_file_declares_the_table_its_name_promises() -> None:
    for path in TABLES.glob("*.tmdl"):
        declared = re.search(r"^table (.+)$", path.read_text(encoding="utf-8"), re.MULTILINE)
        assert declared is not None, f"{path.name} declares no table"
        assert declared.group(1).strip() == path.stem


def test_every_csv_backed_table_has_an_import_partition_with_a_closed_m_query() -> None:
    for name in CSV_BACKED:
        text = table_text(name)
        assert f"partition {name} = m" in text
        assert "mode: import" in text
        assert "source =" in text
        assert text.count("\n\t\t\tlet\n") == 1, f"{name} has no single let block"
        assert re.search(r"^\t\t\tin$", text, re.MULTILINE), f"{name} has no in clause"
        assert text.count("(") == text.count(")"), f"{name} has unbalanced brackets"
        assert text.count("{") == text.count("}"), f"{name} has unbalanced braces"


def test_every_partition_reads_its_path_from_the_datafolder_parameter() -> None:
    """One place configures the path. That is the whole point of the parameter."""
    expressions = (DEFINITION / "expressions.tmdl").read_text(encoding="utf-8")
    assert "expression DataFolder" in expressions
    assert "IsParameterQuery=true" in expressions
    for name in CSV_BACKED:
        text = table_text(name)
        assert "Text.TrimEnd(DataFolder" in text, f"{name} does not use DataFolder"
        assert f'\\{name}.csv' in text
        assert "C:\\Users" not in text, f"{name} hard-codes a path"


def test_relationships_point_at_columns_that_exist() -> None:
    text = (DEFINITION / "relationships.tmdl").read_text(encoding="utf-8")
    pairs = re.findall(r"^\t(?:from|to)Column: (\w+)\.(\w+)$", text, re.MULTILINE)
    assert len(pairs) == 18, "nine relationships, two columns each"
    for table, column in pairs:
        assert (TABLES / f"{table}.tmdl").is_file(), table
        assert f"\n\tcolumn {column}\n" in table_text(table), f"{table}.{column}"


def test_a_column_sorted_by_another_column_sorts_by_one_in_the_same_table() -> None:
    for path in TABLES.glob("*.tmdl"):
        text = path.read_text(encoding="utf-8")
        declared = set(re.findall(r"^\tcolumn (\w+)$", text, re.MULTILINE))
        for target in re.findall(r"^\t\tsortByColumn: (\w+)$", text, re.MULTILINE):
            assert target in declared, f"{path.name} sorts by missing column {target}"


# ---------------------------------------------------------------------------
# The check that matters: the model and the exporter agree on columns
# ---------------------------------------------------------------------------


def exported_header(name: str) -> list[str]:
    with (EXPORT / f"{name}.csv").open(encoding="utf-8", newline="") as handle:
        return next(csv.reader(handle))


@pytest.mark.parametrize("name", CSV_BACKED)
def test_the_model_declares_exactly_the_columns_the_exporter_writes(name: str) -> None:
    text = table_text(name)
    declared = re.findall(r"^\tcolumn (\w+)$", text, re.MULTILINE)
    assert declared == exported_header(name), (
        f"{name}.tmdl and {name}.csv have drifted apart. "
        f"Model: {declared}. Export: {exported_header(name)}."
    )


@pytest.mark.parametrize("name", CSV_BACKED)
def test_every_column_binds_to_its_own_source_column(name: str) -> None:
    text = table_text(name)
    pairs = re.findall(
        r"^\tcolumn (\w+)\n(?:\t\t.+\n)*?\t\tsourceColumn: (\w+)$", text, re.MULTILINE
    )
    assert len(pairs) == len(exported_header(name))
    for column, source in pairs:
        assert column == source, f"{name}.{column} binds to {source}"


@pytest.mark.parametrize("name", CSV_BACKED)
def test_the_m_query_types_every_exported_column(name: str) -> None:
    text = table_text(name)
    typed = set(re.findall(r'^\t{3} +\{"(\w+)", ', text, re.MULTILINE))
    assert typed == set(exported_header(name)), (
        f"{name}: the M query types {sorted(typed)} but the csv has "
        f"{sorted(exported_header(name))}"
    )


# ---------------------------------------------------------------------------
# Measures
# ---------------------------------------------------------------------------


def measures_text() -> str:
    return table_text("_Measures")


def measure_names() -> list[str]:
    return re.findall(r"^\tmeasure '([^']+)' =", measures_text(), re.MULTILINE)


def test_the_measures_the_brief_asks_for_all_exist() -> None:
    expected = {
        "Total Cost EUR",
        "Cost (Selected Scenario and Branch)",
        "Materiality Ratio",
        "Band",
        "Exposure by Tier",
        "Exposure Carrying RATING_GAP",
        "Top 20 Rank by Ratio",
        "2027 Cash-Out",
        "Cash-Out % of Working Capital",
        "Best Case Cost EUR",
        "Worst Case Cost EUR",
        "Flag Count",
        "% Borrowers Estimated",
    }
    assert expected <= set(measure_names())


def test_every_measure_carries_a_description_citing_its_source() -> None:
    """CLAUDE.md section 7, applied to DAX.

    A measure with no description is a number with no provenance, which is
    exactly what the traceability rule forbids.
    """
    text = measures_text()
    blocks = re.findall(
        r"((?:^\t///.*\n)+)\tmeasure '([^']+)' =", text, re.MULTILINE
    )
    described = {name for _, name in blocks}
    assert described == set(measure_names()), (
        f"no description on {sorted(set(measure_names()) - described)}"
    )
    for description, name in blocks:
        assert len(description.split()) >= 12, f"{name} has a description but not a reason"


def test_measures_that_need_a_config_number_read_it_from_the_meta_table() -> None:
    """No measure invents a year, a default scenario or a default branch."""
    text = measures_text()
    for key in (
        "liquidity_shock_year",
        "default_scenario",
        "default_branch",
        "data_label",
    ):
        assert f'meta[key], "{key}"' in text, f"no measure reads meta[{key}]"
    # The band boundaries must not be restated in DAX, where they could drift
    # away from config/thresholds.yaml.
    for boundary in ("0.01", "0.03", "0.06", "0.12"):
        assert boundary not in text, f"a band boundary {boundary} is hard-coded in DAX"


def test_every_measure_reference_points_at_a_measure_that_exists() -> None:
    # Only the DAX. The placeholder column and its M partition use square
    # brackets for something else entirely, and the descriptions name model
    # objects in prose, where a stale reference is a documentation bug rather
    # than a broken measure.
    body = measures_text().split("\n\tcolumn Placeholder")[0]
    text = "\n".join(
        line for line in body.splitlines() if not line.lstrip().startswith("///")
    )
    names = set(measure_names())
    tables = {path.stem for path in TABLES.glob("*.tmdl")}
    for reference in set(re.findall(r"(?<![\w'\]])\[([^\[\]]+)\]", text)):
        if reference.startswith("@"):
            continue  # a column added by ADDCOLUMNS inside the same expression
        assert reference in names, f"measure reference [{reference}] does not exist"
    # And every table-qualified column reference points at a real column.
    for table, column in set(re.findall(r"(\w+)\[(\w+)\]", text)):
        if table in names or table.startswith("@"):
            continue
        assert table in tables, f"unknown table {table} in a measure"
        assert f"\n\tcolumn {column}\n" in table_text(table), f"{table}[{column}]"


def test_measure_expressions_have_balanced_brackets() -> None:
    for block in re.split(r"^\tmeasure ", measures_text(), flags=re.MULTILINE)[1:]:
        expression = block.split("\n\t\tformatString:")[0].split("\n\t\tdisplayFolder:")[0]
        assert expression.count("(") == expression.count(")"), expression[:80]


def test_money_measures_carry_a_euro_format_string() -> None:
    text = measures_text()
    for name in ("Total Cost EUR", "Exposure by Tier", "Cash-Out EUR", "2027 Cash-Out"):
        block = text.split(f"measure '{name}' =")[1].split("\n\tmeasure ")[0]
        assert 'formatString: "€"#,##0' in block, name
    for name in ("Materiality Ratio", "Cash-Out % of Working Capital", "% Borrowers Estimated"):
        block = text.split(f"measure '{name}' =")[1].split("\n\tmeasure ")[0]
        assert "formatString: 0" in block and "%" in block, name
