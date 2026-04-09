#!/usr/bin/env python3
"""
validate_corpus.py — Four-gate corpus validation for UPS labor contract entities.
Replaces the 12 diagnose_*.py diagnostic scripts with a single authoritative check.

Exit codes:
  0 — All Gate 1 and Gate 2 checks pass (warnings may exist in Gate 3/4)
  1 — One or more Gate 1 or Gate 2 failures
"""
from __future__ import annotations
import json, re, sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).parent
_ENTITIES_DIR = _ROOT / "UPS_Labor_Contracts_1" / "json_schemas" / "entities"
_CORPUS_SUMMARY = _ROOT / "UPS_Labor_Contracts_1" / "json_schemas" / "corpus_summary.json"

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------
_TITLE_NOISE = re.compile(r'\.indd\s+\d+|^[\d\s\W]+$')
_SECTION_TITLE_NOISE = re.compile(r'^Section \d+: Section \d+$')

# Known UPS contract date formats, ordered most-specific first
_DATE_FORMATS = [
    '%B %d, %Y',  # "August 1, 2023"
    '%B %Y',       # "August 2023"  (month-only)
    '%m/%d/%Y',    # "08/01/2023"
    '%Y-%m-%d',    # "2023-08-01"
]


def _try_parse_date(s: str | None) -> datetime | None:
    """Return a datetime for *s* using known UPS contract date formats, or None."""
    if not s:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            pass
    return None


def _load_entities() -> list[dict]:
    return [
        json.loads(f.read_text(encoding="utf-8"))
        for f in sorted(_ENTITIES_DIR.glob("*.json"))
    ]


def _load_corpus_summary() -> dict:
    return json.loads(_CORPUS_SUMMARY.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Gate 1 — Structural completeness
# ---------------------------------------------------------------------------
def gate1(docs: list[dict]) -> tuple[list[str], list[str]]:
    """Structural completeness check.

    Applies to every document with page_count >= 10 and extraction_method != 'ocr'.
    Failures are ERRORS (cause exit code 1).
    """
    errors: list[str] = []
    warnings: list[str] = []

    for doc in docs:
        name = Path(doc.get("source_file", "unknown")).stem
        pc = doc.get("page_count", 0) or 0
        wc = doc.get("word_count", 0) or 0
        method = doc.get("extraction_method", "")

        if pc < 10 or method == "ocr":
            continue

        articles = doc.get("articles", [])
        sections = doc.get("sections", [])
        title = doc.get("title", "") or ""

        # Check 1: at least one structural unit (article OR section)
        if len(articles) + len(sections) < 1:
            errors.append(
                f"{name} — no articles or sections extracted (page_count={pc})"
            )

        # Check 2: word density >= 50 words per page
        wpp = wc / max(pc, 1)
        if wpp < 50:
            errors.append(
                f"{name} — word density {wpp:.1f} words/page < 50 threshold "
                f"(page_count={pc}, word_count={wc})"
            )

        # Check 3: title is not pure noise (InDesign artifacts, numbers-only, etc.)
        if _TITLE_NOISE.search(title):
            errors.append(f"{name} — noisy title value: {title!r}")

        # Check 4: no repeated-label section titles in sections array
        noisy = [
            s.get("title", "")
            for s in sections
            if _SECTION_TITLE_NOISE.match(s.get("title", ""))
        ]
        if noisy:
            suffix = f" (+{len(noisy) - 1} more)" if len(noisy) > 1 else ""
            errors.append(
                f"{name} — repeated section-title pattern detected: {noisy[0]!r}{suffix}"
            )

    return errors, warnings


# ---------------------------------------------------------------------------
# Gate 2 — Date integrity
# ---------------------------------------------------------------------------
def gate2(docs: list[dict]) -> tuple[list[str], list[str]]:
    """Date integrity check.

    Applies to every document with non-null effective_date AND expiration_date.
    Actual date-ordering reversals (effective > expiration) are ERRORS.
    Same-day dates (dur == 0) and outlier durations (>10yr) are WARNINGS.
    """
    errors: list[str] = []
    warnings: list[str] = []

    for doc in docs:
        name = Path(doc.get("source_file", "unknown")).stem
        eff_s = doc.get("effective_date")
        exp_s = doc.get("expiration_date")
        dur = doc.get("contract_duration_days")

        # Skip documents where one or both dates are absent
        if eff_s is None or exp_s is None:
            continue

        eff_dt = _try_parse_date(eff_s)
        exp_dt = _try_parse_date(exp_s)

        if eff_dt is None:
            errors.append(
                f"{name} — effective_date cannot be parsed: {eff_s!r}"
            )
            continue
        if exp_dt is None:
            errors.append(
                f"{name} — expiration_date cannot be parsed: {exp_s!r}"
            )
            continue

        if eff_dt > exp_dt:
            # Actual date reversal — ERROR
            errors.append(
                f"{name} — effective_date ({eff_s!r}) is AFTER expiration_date ({exp_s!r})"
            )
        elif eff_dt == exp_dt:
            # Same-day: both fields extracted the same date — WARN not error
            warnings.append(
                f"{name} — same-day dates (extraction artifact): "
                f"effective_date == expiration_date == {eff_s!r}"
            )

        # Corpus-wide outlier duration — WARNING only (not error)
        if dur is not None and dur > 3653:
            warnings.append(
                f"{name} — contract_duration_days={dur} exceeds 10-year threshold (3653 days)"
            )

    return errors, warnings


# ---------------------------------------------------------------------------
# Gate 3 — Value parameter coverage (warnings only)
# ---------------------------------------------------------------------------
def gate3(docs: list[dict]) -> tuple[list[str], list[str]]:
    """Value parameter coverage check.

    Warns when documents claim wages/grievance topics but extracted no corresponding
    parameters.  Gate 3 results never affect exit code.
    """
    errors: list[str] = []
    warnings: list[str] = []

    for doc in docs:
        name = Path(doc.get("source_file", "unknown")).stem
        topics = doc.get("key_topics", [])
        wage_p = doc.get("wage_parameters", [])
        time_p = doc.get("time_parameters", [])

        if "wages" in topics and len(wage_p) == 0:
            warnings.append(
                f"{name} — wages in key_topics but wage_parameters is empty"
            )

        if "grievance_arbitration" in topics and len(time_p) == 0:
            warnings.append(
                f"{name} — grievance_arbitration in key_topics but time_parameters is empty"
            )

    return errors, warnings


# ---------------------------------------------------------------------------
# Gate 4 — Statistical sanity (warnings only)
# ---------------------------------------------------------------------------
def gate4(summary: dict) -> tuple[list[str], list[str]]:
    """Statistical sanity check against corpus_summary.json statistics.

    Gate 4 results never affect exit code.
    """
    errors: list[str] = []
    warnings: list[str] = []

    stats = summary.get("statistics", {})
    tco = stats.get("topic_co_occurrence", {})

    # wages ↔ health_and_welfare co-occurrence — check both key orderings
    hw_wages = tco.get("health_and_welfare", {}).get("wages", None)
    wages_hw = tco.get("wages", {}).get("health_and_welfare", None)
    cooc_val: int = (
        hw_wages if hw_wages is not None else (wages_hw if wages_hw is not None else 0)
    )

    if cooc_val < 25:
        warnings.append(
            f"health_and_welfare↔wages co-occurrence = {cooc_val} (expected >= 25); "
            "possible topic-detection regression"
        )

    # articles_per_document coefficient-of-variation check
    apd = stats.get("articles_per_document", {})
    apd_mean: float = apd.get("mean", 0.0)
    apd_stdev: float = apd.get("stdev", 0.0)

    if apd_stdev >= apd_mean:
        warnings.append(
            f"articles_per_document stdev={apd_stdev} >= mean={apd_mean}; "
            "article detection may have collapsed"
        )

    return errors, warnings


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------
_COL_WIDTH = 60


def _status_line(label: str, errors: list[str], warnings: list[str]) -> str:
    """Format a single summary line for one gate."""
    if errors:
        n = len(errors)
        tag = f'{n} ERROR{"S" if n != 1 else ""}'
    elif warnings:
        n = len(warnings)
        tag = f'PASS ({n} WARNING{"S" if n != 1 else ""})'
    else:
        tag = "PASS"
    padded = f"  {label}"
    return f"{padded:<44}{tag}"


def _print_detail_section(
    title: str, errors: list[str], warnings: list[str], always_show: bool = False
) -> None:
    """Print error lines and/or warning lines for a gate section."""
    if errors:
        print(f"  {title} ERRORS:")
        for msg in errors:
            print(f"    {msg}")
    if warnings or always_show:
        print(f"  {title} WARNINGS:")
        if warnings:
            for msg in warnings:
                print(f"    {msg}")
        else:
            print("    (none)")


def print_report(
    g1e: list[str], g1w: list[str],
    g2e: list[str], g2w: list[str],
    g3e: list[str], g3w: list[str],
    g4e: list[str], g4w: list[str],
) -> None:
    sep = "=" * _COL_WIDTH
    dash = "-" * _COL_WIDTH

    print(sep)
    print("  UPS LABOR CONTRACTS — CORPUS VALIDATION REPORT")
    print(sep)
    print(_status_line("Gate 1 — Structural Completeness", g1e, g1w))
    print(_status_line("Gate 2 — Date Integrity", g2e, g2w))
    print(_status_line("Gate 3 — Value Parameter Coverage", g3e, g3w))
    print(_status_line("Gate 4 — Statistical Sanity", g4e, g4w))
    print(dash)

    # Always show Gate 3 and Gate 4 detail sections (per sample output format)
    _print_detail_section("Gate 1", g1e, g1w, always_show=False)
    _print_detail_section("Gate 2", g2e, g2w, always_show=False)
    _print_detail_section("Gate 3", g3e, g3w, always_show=True)
    _print_detail_section("Gate 4", g4e, g4w, always_show=True)

    print(sep)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> int:
    docs = _load_entities()
    summary = _load_corpus_summary()

    g1e, g1w = gate1(docs)
    g2e, g2w = gate2(docs)
    g3e, g3w = gate3(docs)
    g4e, g4w = gate4(summary)

    print_report(g1e, g1w, g2e, g2w, g3e, g3w, g4e, g4w)

    return 1 if (g1e or g2e) else 0


if __name__ == "__main__":
    sys.exit(main())
