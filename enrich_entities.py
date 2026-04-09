"""
enrich_entities.py — Generate enriched JSON schemas for effectively processed contracts.

Quality filter (considered "effectively and accurately processed"):
  • extraction_method == "pypdf-tier0"
  • word_count / page_count >= 30
  • len(articles) + len(sections) >= 1

Enrichment (money):
  • wage_value_stats   — mean ± 3σ range over wage_parameters[*].value
  • benefit_value_stats — mean ± 3σ range over benefit_parameters[*].value (non-zero values)

Outputs to:  UPS_Labor_Contracts_1/json_schemas/enriched_entities/
"""

import json
import os
import glob
import math
import statistics
from datetime import datetime

INPUT_DIR  = "UPS_Labor_Contracts_1/json_schemas/entities"
OUTPUT_DIR = "UPS_Labor_Contracts_1/json_schemas/enriched_entities"

SIGMA = 3  # range expressed as ±3 standard deviations

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _money_stats(values):
    """
    Return a stats dict for a list of numeric monetary values.

    With n == 1 the stdev is undefined; we store 0.0 and the range
    collapses to the single value ± 0, which is the most honest
    representation when only one data point exists.
    """
    n = len(values)
    if n == 0:
        return None
    mean = statistics.mean(values)
    stdev = statistics.stdev(values) if n >= 2 else 0.0
    variance = stdev ** 2          # included for completeness
    return {
        "n":          n,
        "mean":       round(mean,  4),
        "stdev":      round(stdev, 4),
        "variance":   round(variance, 4),
        "range_low":  round(mean - SIGMA * stdev, 4),
        "range_high": round(mean + SIGMA * stdev, 4),
        "sigma_used": SIGMA,
    }


def _wage_stats_by_unit(wage_params):
    """
    Build wage_value_stats grouped by unit so mixed hourly/weekly entries
    are not statistically pooled when the denominator differs.
    Returns a dict keyed by unit, or 'all' when only one unit is present.
    """
    by_unit = {}
    for w in wage_params:
        v = w.get("value")
        if v is None or not isinstance(v, (int, float)):
            continue
        u = w.get("unit") or "unknown"
        by_unit.setdefault(u, []).append(float(v))

    if not by_unit:
        return None

    # If every value has the same unit, return a flat dict (backward compat).
    if len(by_unit) == 1:
        unit, values = next(iter(by_unit.items()))
        stats = _money_stats(values)
        if stats:
            stats["currency"] = wage_params[0].get("currency", "USD")
            stats["unit"]     = unit
        return stats

    # Multiple units → return one stats block per unit.
    result = {}
    for unit, values in sorted(by_unit.items()):
        s = _money_stats(values)
        if s:
            s["currency"] = "USD"
            s["unit"]     = unit
            result[unit]  = s
    return result


def _benefit_stats_by_type(benefit_params):
    """
    Build benefit_value_stats grouped by benefit type so pension/h&w figures
    are kept separately.
    """
    by_type = {}
    for b in benefit_params:
        v = b.get("value")
        if v is None or not isinstance(v, (int, float)) or float(v) == 0:
            continue
        t = b.get("type") or "unknown"
        by_type.setdefault(t, []).append(float(v))

    if not by_type:
        return None

    result = {}
    for btype, values in sorted(by_type.items()):
        s = _money_stats(values)
        if s:
            s["currency"] = "USD"
            s["unit"] = (
                benefit_params[0].get("unit", "month")
                if len(by_type) == 1 else "see_type"
            )
            result[btype] = s
    return result


def is_effectively_processed(entity):
    """Return True if the document meets quality thresholds."""
    method = entity.get("extraction_method", "")
    pc     = entity.get("page_count",  0) or 1
    wc     = entity.get("word_count",  0) or 0
    arts   = len(entity.get("articles",  []))
    secs   = len(entity.get("sections",  []))
    wpp    = wc / pc
    return (
        method == "pypdf-tier0"
        and wpp >= 30
        and (arts + secs) >= 1
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    input_paths = sorted(glob.glob(os.path.join(INPUT_DIR, "*.json")))
    if not input_paths:
        print(f"No entity files found in {INPUT_DIR!r}")
        return

    qualified = 0
    skipped   = 0
    enriched  = 0

    enrichment_run_at = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    report_rows = []

    for path in input_paths:
        entity = json.load(open(path, encoding="utf-8"))

        if not is_effectively_processed(entity):
            skipped += 1
            report_rows.append(
                f"  SKIP  {os.path.basename(path)}"
                f"  (method={entity.get('extraction_method','-')}"
                f"  wpp={entity.get('word_count',0)//(entity.get('page_count',0) or 1)}"
                f"  arts={len(entity.get('articles',[]))}"
                f"  secs={len(entity.get('sections',[]))})"
            )
            continue

        qualified += 1

        # ------------------------------------------------------------------
        # Build enrichment blocks
        # ------------------------------------------------------------------
        wage_params    = entity.get("wage_parameters",    [])
        benefit_params = entity.get("benefit_parameters", [])

        wage_stats    = _wage_stats_by_unit(wage_params)    if wage_params    else None
        benefit_stats = _benefit_stats_by_type(benefit_params) if benefit_params else None

        # ------------------------------------------------------------------
        # Compose enriched entity — preserve original, append new keys
        # ------------------------------------------------------------------
        enriched_entity = dict(entity)   # shallow copy keeps all original fields

        # Inject enrichment metadata section
        enriched_entity["enrichment_metadata"] = {
            "enriched_at":   enrichment_run_at,
            "enricher":      "enrich_entities.py",
            "sigma_used":    SIGMA,
            "quality_filter": {
                "extraction_method": "pypdf-tier0",
                "min_words_per_page": 30,
                "min_articles_plus_sections": 1,
            },
        }

        if wage_stats is not None:
            enriched_entity["wage_value_stats"] = wage_stats
            enriched += 1

        if benefit_stats is not None:
            enriched_entity["benefit_value_stats"] = benefit_stats

        # Write to enriched output
        out_name = os.path.basename(path)
        out_path = os.path.join(OUTPUT_DIR, out_name)
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(enriched_entity, fh, indent=2, ensure_ascii=False)

        wage_n    = wage_stats["n"]    if isinstance(wage_stats, dict) and "n" in wage_stats else (
            sum(s["n"] for s in wage_stats.values()) if isinstance(wage_stats, dict) else 0)
        benefit_n = sum(
            s["n"] for s in (benefit_stats or {}).values()
            if isinstance(s, dict) and "n" in s
        ) if isinstance(benefit_stats, dict) else 0

        report_rows.append(
            f"  OK    {out_name}"
            f"  wages={len(wage_params)}(stats_n={wage_n})"
            f"  benefits={len(benefit_params)}(stats_n={benefit_n})"
        )

    # Print summary report
    print("=" * 72)
    print("  ENRICHMENT REPORT")
    print("=" * 72)
    print(f"  Input  : {INPUT_DIR}")
    print(f"  Output : {OUTPUT_DIR}")
    print(f"  Total  : {len(input_paths)} documents")
    print(f"  Qualified (enriched): {qualified}")
    print(f"  Skipped (quality filter): {skipped}")
    print(f"  Documents with wage_value_stats added: {enriched}")
    print("-" * 72)
    for row in report_rows:
        print(row)
    print("=" * 72)


if __name__ == "__main__":
    main()
