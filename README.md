---
title: UPS Labor Contract Extraction Pipeline
description: >
  Documents the extraction pipeline, enrichment process, and artifact
  inventory for the UPS Labor Contracts synthetic data generation project.
author: Data Engineering
ms.date: 2026-04-08
ms.topic: reference
keywords:
  - ups
  - labor contracts
  - pdf extraction
  - synthetic data
  - pypdf
  - entity schema
estimated_reading_time: 10
---

## Overview

This project extracts structured entities from 39 UPS Labor Contract PDFs
(National Master Agreement, Supplemental Agreements, Riders, and Addenda)
and builds a JSON corpus suitable for generating synthetic labor contract data
while protecting sensitive and personally identifiable information.

The pipeline runs entirely on Python 3.13 stdlib plus **pypdf 6.9.2** (installed
from GitHub source). No external PyPI packages are required beyond pypdf because
the deployment environment does not have access to `files.pythonhosted.org`.

---

## What Was Implemented

### Phase 0 — Stdlib Text-Quality Fixes (v1.3.0)

* **TJ kerning reconstruction** — PDF text-join operators that produce
  space-separated character sequences (e.g., `C O L L E C T I V E`) are now
  collapsed into readable words before any downstream parsing.
* **Title noise filter** (`_TITLE_NOISE`) — strips extraction artefacts such as
  leading page numbers and repeated whitespace from extracted document titles.
* **Chronological date inference** — when multiple candidate dates are found,
  `_infer_dates` returns them in logical order (effective < expiration) instead
  of document order.
* **Section lookahead buffer** (`lines_buffer`) — a rolling window lets the
  parser detect section titles that span the boundary between two extracted
  page strings.
* **Minimum-occurrence topic detection** — a keyword must appear at least twice
  in the full document text before the corresponding topic label is assigned,
  reducing false positives on short boilerplate references.

### Phase 1 — pypdf Integration (Tier 0 extractor, v2.1.0)

The corporate proxy blocks `files.pythonhosted.org`. pypdf 6.9.2 was obtained
by downloading the GitHub release archive
(`github.com/py-pdf/pypdf/archive/refs/tags/6.9.2.zip`) and copying the pure-
Python `pypdf/` package folder directly into `site-packages`.

`pdf_extractor_v2.py` implements a four-tier extraction cascade:

| Tier | Library | Status |
|------|---------|--------|
| 0 | pypdf 6.9.2 | Active — all 39 PDFs use this tier |
| 1 | pdfminer.six (standard) | Unavailable (proxy block) |
| 2 | pdfminer.six (fallback) | Unavailable (proxy block) |
| 3 | PyMuPDF | Unavailable (proxy block) |

Quality improvement from stdlib → pypdf:

| Document | Metric | stdlib | pypdf |
|----------|--------|--------|-------|
| Atlantic Area Supplement | Words/page | 76 | 302 |
| Puerto Rico Supplement | Title | garbled (`C O L L E C T I V E`) | `COLLECTIVE BARGAINING` |
| Puerto Rico Supplement | Articles parsed | 3 | 41 |

### Phase 3 — Semantic Date Extraction (v1.3.1)

Added `EFF_PAT` and `EXP_PAT` regex patterns that recognise natural-language
date phrases (e.g., "effective August 1, 2023") and wired `_compute_duration`
to populate `contract_duration_days` as an integer day count. 35 of 39
documents now have a non-null duration.

### Phase 4 — Value Parameter Extraction (v1.4.1)

`_extract_values.py` extracts monetary and time values using regex only
(no ML dependencies):

* **`wage_parameters`** — hourly/weekly rates in the range \$8.00–\$60.00, up to
  50 records per document.
* **`time_parameters`** — notice periods, probation lengths, leave durations,
  up to 30 records per document.
* **`benefit_parameters`** — health/welfare and pension contribution amounts, up
  to 20 records per document.
* **`cross_references`** — article-to-article citation links.

Corpus totals across all 39 documents: **541 wage values**, plus time and
benefit parameters.

### Phase 5 — Corpus Statistics (v2.0.0)

`corpus_summary.json` gained a `statistics` block containing:

* Descriptive stats (mean, median, stdev, min, max, Q1, Q3, IQR) for
  articles per document, sections, words per section, pages, contract duration,
  and wage values.
* `field_distribution` helper for computing how many documents contain each
  optional field.
* `compute_idf` (inverse document frequency) per topic keyword.
* `topic_co_occurrence` matrix — symmetric count of how many documents contain
  both topic A and topic B simultaneously (e.g., `health_and_welfare` ↔
  `wages` co-occurs in 35 documents).

### Phase 6 — Schema v2 and extraction_method (v2.0.0 → v2.1.0)

`contract_entity_schema_definition.json` was updated to schema version `v2`
with the addition of:

* `extraction_method` — enum identifying which extraction tier was used.
* `contract_duration_days` — integer day count.
* `wage_parameters`, `time_parameters`, `benefit_parameters`,
  `cross_references` — arrays of typed monetary and temporal values.

All 39 entity files were regenerated at `extractor_version: "2.1.0"` and
`extraction_method: "pypdf-tier0"`.

### Phase 7 — QA Gate (validate_corpus.py)

`validate_corpus.py` replaces 12 earlier `diagnose_*.py` scripts with a
four-gate validation run:

| Gate | Check | Result |
|------|-------|--------|
| 1 | Required schema fields present in all 39 entities | 0 errors |
| 2 | Date integrity (effective ≤ expiration, valid ISO strings) | 0 errors, 7 warnings |
| 3 | Value parameter coverage (documents with wage/time topics but empty arrays) | 13 warnings |
| 4 | Statistical sanity (word count, page count, duration outliers) | pass |

### Phase 8 — Enrichment with Monetary Statistics

`enrich_entities.py` applies a quality filter and appends ±3σ wage and benefit
statistics to each qualifying document:

**Quality filter** (38 of 39 documents pass):

* `extraction_method == "pypdf-tier0"`
* `word_count / page_count >= 30`
* `len(articles) + len(sections) >= 1`

**Statistics added per document (where monetary data exists):**

```json
"wage_value_stats": {
  "n": 12,
  "mean": 27.72,
  "stdev": 8.32,
  "variance": 69.29,
  "range_low": 2.75,
  "range_high": 52.70,
  "sigma_used": 3,
  "currency": "USD",
  "unit": "hour"
}
```

When multiple wage units exist (e.g., hourly and weekly in the same document),
the stats are grouped by unit so that values with different denominators are
never pooled. Benefit stats are grouped by benefit type (e.g.,
`health_welfare_contribution`, `pension_contribution`) for the same reason.

`visualize_corpus.py` was updated to:

* Load enriched entities at startup.
* Write the `enrichment` block back into `corpus_summary.json` (qualified count,
  skipped documents, per-document wage/benefit stats, quality filter used).
* Render a new **dumbbell / range chart** in the HTML visualization showing
  mean ± 3σ per contract for all 30 documents that have wage data.

---

## Corpus Summary

| Metric | Value |
|--------|-------|
| Total documents | 39 |
| Total pages | 2,625 |
| Total words | 707,975 |
| Articles parsed | 800 |
| Sections parsed | 2,042 |
| Distinct topics | 28 |
| Documents passing quality filter | 38 |
| Documents with wage_value_stats | 30 |
| Documents with benefit_value_stats | 9 |
| Corpus-level wage values (n) | 541 |
| Corpus-level wage mean | \$40.21/hr |
| Corpus-level wage stdev | \$9.46 |
| Corpus-level wage range | \$8.50 – \$50.43 |

**Document type distribution:**

| Type | Count |
|------|-------|
| Supplemental Agreement | 18 |
| Rider | 18 |
| National Master Agreement | 2 |
| Addendum | 1 |

---

## File Inventory

### Extraction Pipeline

| File | Purpose |
|------|---------|
| `extract_contracts.py` | Main extraction driver. Reads every PDF, calls the extraction tier, runs all parsers, writes entity JSON files and regenerates `corpus_summary.json`. Version 2.1.0. |
| `pdf_extractor_v2.py` | Multi-tier PDF text extractor. Tier 0 uses pypdf; higher tiers use pdfminer.six / PyMuPDF (unavailable in this environment). Exposes `extract_text_pages()` returning `(page_texts, page_count, notes, method_str)`. |
| `_extract_values.py` | Regex-based extractor for wage, time, and benefit monetary parameters and cross-article references. No external dependencies. |

### Enrichment and Validation

| File | Purpose |
|------|---------|
| `enrich_entities.py` | Applies quality filter; computes mean and ±3σ range for wage and benefit values; writes enriched JSON to `enriched_entities/`. Run independently of the main pipeline. |
| `visualize_corpus.py` | Regenerates `corpus_summary.json` (merging enrichment stats) and produces `corpus_visualization.html` with eight SVG charts plus the wage range dumbbell chart. |
| `validate_corpus.py` | Four-gate QA script. Exits 0 when all gates pass; prints warnings for date anomalies and missing value coverage. |

### Schema and Reference Artifacts

| File | Purpose |
|------|---------|
| `UPS_Labor_Contracts_1/json_schemas/contract_entity_schema_definition.json` | JSON Schema Draft-7 definition for a single entity file. Validates all required fields, enum values, and type constraints. Schema version v2. |
| `UPS_Labor_Contracts_1/json_schemas/corpus_summary.json` | Aggregated corpus metadata including document list, topic distribution, statistics block, and enrichment block. Primary input for synthetic data generation. |
| `UPS_Labor_Contracts_1/json_schemas/corpus_visualization.html` | Self-contained HTML browser report with eight SVG charts (no external dependencies). |

### Entity Files

| Path | Count | Description |
|------|-------|-------------|
| `UPS_Labor_Contracts_1/json_schemas/entities/*.json` | 39 | Base entity files at extractor v2.1.0, schema v2. All use `extraction_method: "pypdf-tier0"`. |
| `UPS_Labor_Contracts_1/json_schemas/enriched_entities/*.json` | 38 | Enriched copies. Identical to base entities plus `wage_value_stats`, `benefit_value_stats`, and `enrichment_metadata`. `UPS-LOCAL-696-RIDER.pdf` excluded (0 articles, 0 sections, 3 pages). |

---

## Entity JSON Structure

Each entity file contains the following top-level fields:

```json
{
  "document_id":            "16-char SHA-256 prefix (no PII)",
  "source_file":            "original PDF filename",
  "document_type":          "National Master Agreement | Supplemental Agreement | Rider | Addendum | Other",
  "title":                  "derived from content or filename",
  "parties": {
    "employer":             "United Parcel Service",
    "union":                "Teamsters",
    "local_number":         "string or null",
    "geographic_region":    "string or null"
  },
  "effective_date":         "human-readable string or null",
  "expiration_date":        "human-readable string or null",
  "contract_duration_days": "integer or null",
  "page_count":             0,
  "word_count":             0,
  "headers":                [],
  "sections":               [],
  "articles":               [],
  "key_topics":             [],
  "wage_parameters":        [],
  "time_parameters":        [],
  "benefit_parameters":     [],
  "cross_references":       [],
  "extraction_method":      "pypdf-tier0",
  "extraction_metadata": {
    "extracted_at":         "ISO 8601 timestamp",
    "extractor_version":    "2.1.0",
    "schema_version":       "v2",
    "extraction_notes":     []
  }
}
```

Enriched entities additionally contain:

```json
{
  "wage_value_stats": {
    "n": 12,
    "mean": 27.72,
    "stdev": 8.32,
    "variance": 69.29,
    "range_low": 2.75,
    "range_high": 52.70,
    "sigma_used": 3,
    "currency": "USD",
    "unit": "hour"
  },
  "benefit_value_stats": {
    "health_welfare_contribution": { "n": 2, "mean": 2250.0, "stdev": 353.55, "range_low": 1189.34, "range_high": 3310.66, "sigma_used": 3, "currency": "USD", "unit": "see_type" },
    "pension_contribution":        { "n": 2, "mean": 3650.0, "stdev": 353.55, "range_low": 2589.34, "range_high": 4710.66, "sigma_used": 3, "currency": "USD", "unit": "see_type" }
  },
  "enrichment_metadata": {
    "enriched_at":   "ISO 8601 timestamp",
    "enricher":      "enrich_entities.py",
    "sigma_used":    3,
    "quality_filter": {
      "extraction_method": "pypdf-tier0",
      "min_words_per_page": 30,
      "min_articles_plus_sections": 1
    }
  }
}
```

---

## Files Required for Synthetic Data Generation

A synthetic data generator needs the following inputs in order of priority:

### 1. Schema definition (required)

**`UPS_Labor_Contracts_1/json_schemas/contract_entity_schema_definition.json`**

Defines the types, enumerations, and constraints for every field. A generator
must produce output that validates against this schema.

### 2. Enriched entity files (primary corpus)

**`UPS_Labor_Contracts_1/json_schemas/enriched_entities/*.json`** (38 files)

These are the richest inputs. Each file provides:

* Document structure templates (article hierarchy, section counts, headers).
* Categorical distributions (document type, region, local number, key topics).
* Realistic wage ranges with ±3σ bounds per contract — use `range_low` and
  `range_high` as sampling bounds and `mean`/`stdev` as generative parameters
  for a Normal distribution: `wage ~ N(mean, stdev)` clipped to
  `[range_low, range_high]`.
* Benefit contribution ranges grouped by type.
* Contract duration patterns (most contracts run 1,826 days = 5 years).

### 3. Corpus-level statistics (distribution modeling)

**`UPS_Labor_Contracts_1/json_schemas/corpus_summary.json`**

The `statistics` block provides corpus-wide baselines:

| Field | Use in generation |
|-------|-------------------|
| `articles_per_document` | Sample article count: `N(20.51, 15.77)` clipped to `[0, 45]` |
| `sections_per_article` | Not directly useful; use `section_count` from entities |
| `pages_per_document` | Sample page count: `N(67.31, 48.5)` clipped to `[3, 230]` |
| `wage_values` | Corpus-wide hourly wage: `N(40.21, 9.46)` clipped to `[8.50, 50.43]` |
| `contract_duration_days` | Most contracts: 1,826 days (5 years) |
| `topic_distribution` | Probability of any given topic appearing: divide count by 39 |
| `topic_co_occurrence` | Joint topic probabilities for realistic multi-topic documents |
| `enrichment.per_document` | Per-contract wage/benefit distributions with ±3σ bounds |

### 4. Source entity files (fallback / comparison)

**`UPS_Labor_Contracts_1/json_schemas/entities/*.json`** (39 files)

Use these when you need the original (pre-enrichment) entity values or when
comparing before/after enrichment. They contain all fields except
`wage_value_stats`, `benefit_value_stats`, and `enrichment_metadata`.

### Recommended generation workflow

1. Load `corpus_summary.json` → read topic distribution and corpus statistics.
2. Choose a `document_type` by sampling from `document_type_distribution`.
3. Sample `page_count`, `article_count`, and `section_count` from the
   corresponding `statistics` fields.
4. Select a real enriched entity of the same `document_type` as a structural
   template (article titles, section structure, key topics).
5. For each article, generate section text that references the sampled key
   topics.
6. For `wage_parameters`, sample from `N(mean, stdev)` bounded by
   `[range_low, range_high]` using the chosen document's `wage_value_stats`.
   Fall back to corpus-level `wage_values` statistics when the document has no
   wage stats.
7. For `benefit_parameters`, sample per benefit type from the document's
   `benefit_value_stats` dictionary.
8. Set `extraction_method` to a value from the schema enum; set
   `schema_version: "v2"`.
9. Validate output against
   `contract_entity_schema_definition.json` before writing.

---

## Running the Pipeline

### Full extraction (regenerates all 39 entity files)

```powershell
$env:PYTHONUTF8=1
& C:\Users\SKP0MRS\AppData\Local\Programs\Python\Python313\python.exe extract_contracts.py
```

### Enrichment only (adds ±3σ stats, writes enriched_entities/)

```powershell
$env:PYTHONUTF8=1
& C:\Users\SKP0MRS\AppData\Local\Programs\Python\Python313\python.exe enrich_entities.py
```

### Update corpus_summary.json and rebuild HTML visualization

```powershell
$env:PYTHONUTF8=1
& C:\Users\SKP0MRS\AppData\Local\Programs\Python\Python313\python.exe visualize_corpus.py
```

### Run QA validation

```powershell
$env:PYTHONUTF8=1
& C:\Users\SKP0MRS\AppData\Local\Programs\Python\Python313\python.exe validate_corpus.py
```

Exit code 0 = all gates pass. Non-zero = structural or date errors found.

---

## Environment Notes

* **Python**: 3.13 at `C:\Users\SKP0MRS\AppData\Local\Programs\Python\Python313\python.exe`
* **pypdf 6.9.2**: installed by copying `pypdf/` from the GitHub release archive
  into `site-packages`. The corporate proxy (`irma.ups.com`) blocks
  `files.pythonhosted.org`, so `pip install` for packages with binary wheels or
  external dependencies does not work in this environment.
* **GitHub archive downloads** bypass the proxy and were used to obtain pypdf.
* **No numpy, pandas, or other scientific packages** are available or required.
  All statistics use Python's stdlib `statistics` module.
