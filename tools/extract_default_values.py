"""Read the Commission default-values workbook and emit the rows we need.

Run this once, by hand, to regenerate config/emission_defaults.yaml. It is not
imported at app runtime. The workbook itself is a third-party file kept in
data/raw/ and excluded from git.

Usage:
    python tools/extract_default_values.py            # print a JSON summary
    python tools/extract_default_values.py --yaml     # print the by_cn block

The workbook stores European decimal commas as text ("1,370"). Cells reading
"N/A", "-" or "see below" carry no value and become null. "see below" marks a
parent CN heading whose values sit on the child rows underneath it.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterator

import openpyxl

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKBOOK = REPO_ROOT / "data" / "raw" / "CBAM_default_values_definitive_v20260204.xlsx"

# The fallback sheet: values that apply when no origin-specific value is used.
FALLBACK_SHEET = "_Other Countries and Territorie"

# Cell contents that mean "no number here".
NO_VALUE = {"", "n/a", "na", "-", "see below", "not applicable"}

SECTOR_HEADERS = {
    "cement",
    "fertilisers",
    "aluminium",
    "hydrogen",
    "iron and steel",
    "electricity",
}


@dataclass(frozen=True)
class DefaultRow:
    """One CN code row from the default-values table."""

    sector: str
    cn_code: str
    description: str
    direct: float | None
    indirect: float | None
    total: float | None
    production_route: str | None
    sheet_row: int


def parse_number(cell: object) -> float | None:
    """Turn a workbook cell into a float, or None when it carries no value."""
    if cell is None:
        return None
    if isinstance(cell, (int, float)):
        return float(cell)
    text = str(cell).strip()
    if text.lower() in NO_VALUE:
        return None
    # European decimal comma, and thin or normal spaces used as group separators.
    text = text.replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def read_sheet(path: Path, sheet_name: str) -> Iterator[DefaultRow]:
    """Yield one DefaultRow per CN code row on the given sheet."""
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    if sheet_name not in workbook.sheetnames:
        raise KeyError(f"sheet {sheet_name!r} not in {path.name}")
    sheet = workbook[sheet_name]
    sector = ""
    for row_number, row in enumerate(sheet.iter_rows(values_only=True), start=1):
        cells = ["" if c is None else str(c).strip() for c in row]
        if not any(cells):
            continue
        first = cells[0]
        rest_is_empty = not any(cells[1:])
        if rest_is_empty and first.lower() in SECTOR_HEADERS:
            sector = first
            continue
        if rest_is_empty:
            # Sheet title or a stray note.
            continue
        if row_number <= 2:
            # Title and column header rows.
            continue
        direct = parse_number(row[2] if len(row) > 2 else None)
        indirect = parse_number(row[3] if len(row) > 3 else None)
        total = parse_number(row[4] if len(row) > 4 else None)
        if direct is None and indirect is None and total is None:
            # A parent CN heading marked "see below". Its children carry values.
            continue
        route = cells[5] if len(cells) > 5 and cells[5] else None
        yield DefaultRow(
            sector=sector,
            cn_code=first,
            description=cells[1],
            direct=direct,
            indirect=indirect,
            total=total,
            production_route=route,
            sheet_row=row_number,
        )


def summarise(rows: list[DefaultRow]) -> dict[str, dict[str, object]]:
    """Per sector: how many rows, and the spread of the total default value."""
    out: dict[str, dict[str, object]] = {}
    for row in rows:
        bucket = out.setdefault(
            row.sector,
            {"n_cn_codes": 0, "total_min": None, "total_max": None, "has_indirect": False},
        )
        bucket["n_cn_codes"] = int(bucket["n_cn_codes"]) + 1
        if row.indirect is not None:
            bucket["has_indirect"] = True
        if row.total is not None:
            low = bucket["total_min"]
            high = bucket["total_max"]
            bucket["total_min"] = row.total if low is None else min(float(low), row.total)
            bucket["total_max"] = row.total if high is None else max(float(high), row.total)
    return out


# Engine-facing good groups. The keys match the import_t_* columns of
# template_borrowers.csv and the goods keys of config/cbam_rules.yaml.
#
# Each group needs one number, but the workbook gives a number per CN code. We
# do not average them, because an average of CN codes is not a value anyone
# published. Instead each group names one representative CN code and uses that
# code's adopted value. The choice of representative is the author's, so the
# group entries carry a lower data-quality score than the CN rows they copy.
REPRESENTATIVES: dict[str, dict[str, str]] = {
    "cement": {
        "cn_code": "2523 29 00",
        "sector": "Cement",
        "basis": (
            "Grey Portland cement, the ordinary traded cement product. The other "
            "cement rows are clinker, white cement and aluminous cement, which a "
            "general borrower is unlikely to import in bulk."
        ),
    },
    "fertiliser": {
        "cn_code": "3102 10 19",
        "sector": "Fertilisers",
        "basis": (
            "Urea containing more than 45 percent nitrogen, the largest nitrogen "
            "fertiliser product in world trade by tonnage."
        ),
    },
    "steel": {
        "cn_code": "7208",
        "sector": "Iron and steel",
        "basis": (
            "Hot-rolled flat products of iron or non-alloy steel of width 600 mm "
            "or more. The standard commodity steel import. The iron and steel "
            "sector spans 200 CN rows from 0,686 to 7,100 tCO2e per tonne, so a "
            "borrower importing a specific grade should override this value."
        ),
    },
    "aluminium": {
        "cn_code": "7601",
        "sector": "Aluminium",
        "basis": (
            "Unwrought aluminium, the primary traded form and the lowest value in "
            "the sector. Downstream aluminium articles run up to 4,037."
        ),
    },
    "hydrogen": {
        "cn_code": "2804 10 00",
        "sector": "Hydrogen",
        "basis": "The only hydrogen row in the table.",
    },
}

# Groups the engine knows about that the workbook does not cover.
KNOWN_GAPS: dict[str, str] = {
    "electricity": (
        "The workbook Overview sheet states that it does not contain the default "
        "values for electricity as a CBAM good, which sit in Annex III to "
        "Implementing Regulation (EU) 2025/2621. Electricity is also measured in "
        "MWh, not tonnes, so its factor has a different unit."
    ),
}

DATA_QUALITY_SCALE = {
    5: "Adopted value copied for the exact CN code the borrower imports.",
    4: "Adopted value for the parent CN heading of the code the borrower imports.",
    3: "Adopted value of a representative CN code chosen by the author to stand "
       "for a whole sector group. Correct for that code, approximate for the group.",
    2: "Value derived or scaled by the author from an adopted value.",
    1: "No adopted value available. See the TODO on the entry.",
}


def yaml_quote(text: str) -> str:
    """Quote a string for YAML, escaping the few characters that matter."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def fmt(value: float | None) -> str:
    return "null" if value is None else repr(value)


def build_config(rows: list[DefaultRow]) -> str:
    """Render config/emission_defaults.yaml from the extracted rows."""
    by_code = {r.cn_code: r for r in rows}
    stats = summarise(rows)
    lines: list[str] = []
    add = lines.append

    add("# emission_defaults.yaml")
    add("#")
    add("# Default embedded emission values in tCO2e per tonne of good.")
    add("#")
    add("# GENERATED by tools/extract_default_values.py from the Commission")
    add("# default-values workbook. Do not edit by hand. To change a value, change")
    add("# the source file or the generator, then rerun it.")
    add("#")
    add("# Two blocks:")
    add("#   good_groups  one entry per group the engine prices, keyed to the")
    add("#                import_t_* columns of template_borrowers.csv. Each names a")
    add("#                representative CN code and copies that code's adopted value.")
    add("#   by_cn        every CN code row on the fallback sheet, exactly as")
    add("#                published. This is the audit trail behind good_groups.")
    add("#")
    add("# Sign conventions: indirect null means the table gives no indirect value")
    add("# for that code, which for iron and steel, aluminium and hydrogen reflects")
    add("# that indirect emissions are not covered for those goods.")
    add("")
    add("meta:")
    add('  config_version: "1.0.0"')
    add('  last_updated: "2026-09-14"')
    add('  generated_by: "tools/extract_default_values.py"')
    add(f"  source_sheet: {yaml_quote(FALLBACK_SHEET)}")
    add("  source_workbook: " + yaml_quote(WORKBOOK.name))
    add("  source_regulation: " + yaml_quote(
        "Annex I to Commission Implementing Regulation (EU) 2025/2621, as "
        "corrected by Commission Implementing Regulation (EU) 2026/1740. "
        "Applies from 1 January 2026."
    ))
    add("  workbook_version: " + yaml_quote(
        "Version 2, dated 2026-08-06 per the workbook Version History sheet, "
        "based on Annexes I and II to Implementing Regulation (EU) 2026/1740 "
        "adopted 20 July 2026. Note the cached filename carries the version 1 "
        "date of 2026-02-04. The content is version 2."
    ))
    add("  basis: " + yaml_quote(
        "The fallback sheet, that is the values that apply when origin specific "
        "values are not used. Origin specific sheets exist per exporting country "
        "and are not used by this tool."
    ))
    add("  decimal_note: " + yaml_quote(
        "The workbook writes decimals with European commas. Converted to points. "
        "Cells reading N/A, - or see below carry no value and become null."
    ))
    add("  caveat: " + yaml_quote(
        "The workbook Overview disclaimer, written for version 1, says the file "
        "excludes indirect default values. The version 2 history entry says the "
        "file is based on Annexes I and II, and the sheet does carry indirect "
        "values for cement and fertilisers. The indirect values below are "
        "recorded as found. Verify against the Official Journal text before any "
        "use beyond screening."
    ))
    add("")
    add("data_quality_scale:")
    for score in sorted(DATA_QUALITY_SCALE, reverse=True):
        add(f"  {score}: {yaml_quote(DATA_QUALITY_SCALE[score])}")
    add("")
    add("good_groups:")

    for key, spec in REPRESENTATIVES.items():
        row = by_code[spec["cn_code"]]
        sector_stats = stats[spec["sector"]]
        add(f"  {key}:")
        add(f"    label: {yaml_quote(row.description[:90])}")
        add(f"    sector: {yaml_quote(row.sector)}")
        add(f"    representative_cn: {yaml_quote(row.cn_code)}")
        add("    direct:")
        add(f"      value: {fmt(row.direct)}")
        add('      unit: "tCO2e/t"')
        add("    indirect:")
        add(f"      value: {fmt(row.indirect)}")
        add('      unit: "tCO2e/t"')
        add("    total:")
        add(f"      value: {fmt(row.total)}")
        add('      unit: "tCO2e/t"')
        add("    sector_total_range:")
        add(f"      min: {fmt(sector_stats['total_min'])}")
        add(f"      max: {fmt(sector_stats['total_max'])}")
        add(f"      n_cn_codes: {sector_stats['n_cn_codes']}")
        add(f"    selection_basis: {yaml_quote(spec['basis'])}")
        add("    source: " + yaml_quote(
            f"Annex I to IR (EU) 2025/2621 as corrected by IR (EU) 2026/1740, "
            f"workbook sheet {FALLBACK_SHEET!r}, row {row.sheet_row}, "
            f"CN {row.cn_code}."
        ))
        add("    status: adopted")
        add("    data_quality: 3")
        add("")

    for key, reason in KNOWN_GAPS.items():
        add(f"  {key}:")
        add(f"    label: {yaml_quote('Electricity')}")
        add('    sector: "Electricity"')
        add("    representative_cn: null")
        add("    direct:")
        add("      value: null")
        add('      unit: "tCO2e/MWh"')
        add("    indirect:")
        add("      value: null")
        add('      unit: "tCO2e/MWh"')
        add("    total:")
        add("      value: null")
        add('      unit: "tCO2e/MWh"')
        add("    sector_total_range: null")
        add("    selection_basis: null")
        add("    source: null")
        add("    status: TODO")
        add(f"    todo: {yaml_quote(reason)}")
        add("    todo_document_needed: " + yaml_quote(
            "Annex III to Commission Implementing Regulation (EU) 2025/2621, "
            "default values for electricity as a CBAM good, as corrected. Fetch "
            "the Official Journal text and record tCO2e per MWh per origin."
        ))
        add("    data_quality: 1")
        add("")

    add("# Every CN code row on the fallback sheet, exactly as published.")
    add("by_cn:")
    for row in rows:
        add(f"  {yaml_quote(row.cn_code)}:")
        add(f"    sector: {yaml_quote(row.sector)}")
        add(f"    description: {yaml_quote(row.description[:160])}")
        add(f"    direct: {fmt(row.direct)}")
        add(f"    indirect: {fmt(row.indirect)}")
        add(f"    total: {fmt(row.total)}")
        add(f"    sheet_row: {row.sheet_row}")
    add("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sheet", default=FALLBACK_SHEET)
    parser.add_argument("--yaml", action="store_true", help="print the by_cn yaml block")
    parser.add_argument("--summary", action="store_true", help="print per sector summary")
    parser.add_argument(
        "--write-config",
        action="store_true",
        help="write config/emission_defaults.yaml",
    )
    args = parser.parse_args()

    if not WORKBOOK.is_file():
        print(f"workbook not found: {WORKBOOK}", file=sys.stderr)
        print("It is a third-party file kept out of git. See data/SOURCES.md.", file=sys.stderr)
        return 2

    rows = list(read_sheet(WORKBOOK, args.sheet))

    if args.write_config:
        target = REPO_ROOT / "config" / "emission_defaults.yaml"
        target.write_text(build_config(rows), encoding="utf-8")
        print(f"wrote {target} from {len(rows)} CN rows")
        return 0

    if args.summary:
        print(json.dumps(summarise(rows), indent=2))
        return 0

    if args.yaml:
        for row in rows:
            key = row.cn_code.replace(" ", "")
            print(f'  "{key}":')
            print(f"    sector: {row.sector}")
            print(f"    cn_code: \"{row.cn_code}\"")
            print(f"    direct: {row.direct if row.direct is not None else 'null'}")
            print(f"    indirect: {row.indirect if row.indirect is not None else 'null'}")
            print(f"    total: {row.total if row.total is not None else 'null'}")
            print(f"    sheet_row: {row.sheet_row}")
        return 0

    print(json.dumps([asdict(r) for r in rows], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
