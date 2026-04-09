---
title: "UPS Labor Contract Extraction Plan v2"
description: "Critical review of the current extraction implementation and a phased redesign plan for accurate parameter extraction suitable for synthetic data generation."
author: "GitHub Copilot"
ms.date: "2026-04-08"
ms.topic: "reference"
keywords: ["pdf extraction", "synthetic data", "labor contracts", "NLP", "parameter extraction"]
---

## Critical Review of the Current Implementation

The current extractor in `extract_contracts.py` is a zero-dependency stdlib solution
that runs without installing any packages. That constraint is its most significant
design achievement and its most significant liability. The gaps below each trace back
to that single choice.

### Gap 1: TJ Array Text Assembly Produces Garbled Words

The `_parse_content_stream` function collects hex and literal fragments from `TJ`
arrays and joins them with a space. PDF producers using InDesign apply sub-word
character kerning by slicing individual glyphs into separate TJ array elements.
When those fragments are space-joined, every word that was kerned becomes
fragmented: `"T ractor-T railer"`, `"COLL E C T IVE"`, `"V acancies"`.

The correct rule is that fragments inside a TJ array with a numeric displacement
below approximately +250 text-space units belong to the same word and must be
concatenated without a space. Only displacements greater than the inter-word
threshold should introduce a space. Without access to the current font size and
text matrix, this threshold cannot be calculated, so the assembly decision is
made blindly.

**Impact on downstream extraction:** Garbled words break every regex that depends
on word boundaries. `ARTICLE_PAT` and `SECTION_PAT` cannot match `"ART ICLE"`.
Topic keywords cannot match `"griev ance"`. Monetary patterns cannot match
`"$21. 77"`. Any document produced by InDesign or a kerning-heavy PDF workflow
propagates this corruption through every output field.

### Gap 2: CID Font Encoding Is Silently Skipped

Two documents (Latin America Rider, Puerto Rico Supplement) use CID fonts with
custom glyph-to-character mappings. The `_decode_hex_pdf_string` function applies
a UTF-16 BE heuristic and falls back to Latin-1. For CID fonts without a
`/ToUnicode` CMap, the decoded bytes are glyph IDs, not Unicode code points.
The printability check (`> 0.5 printable`) accepts garbage output because glyph
IDs frequently fall in printable ASCII ranges.

The extractor has no mechanism to detect or parse `/ToUnicode` stream entries
(`/ToUnicode` is present in raw bytes and flagged by `diagnose_pdfs.py` but never
read). The `add_extraction_notes.py` post-processor documents the limitation as a
note but does not correct the extracted text.

**Impact on downstream extraction:** The Latin America Rider produces 3,076 words
against an expected 20,000+ from its 26 pages. The Puerto Rico Supplement
produces a title of `"COLL E C T IVE"` and 2 articles against an expected 25+,
despite having 88,661 correctly decoded body words because headings use a
different font.

### Gap 3: Page Distribution Is a Linear Approximation

`extract_text_from_pdf` collects all text from all streams into a flat list and
divides it into equal-sized chunks, one chunk per page. This approach has two
consequences. First, the page number attributed to any heading or value is wrong
by an unpredictable amount because stream order does not match reading order.
Second, the page-level text used by `_extract_articles_sections` is
a fictitious slice, not actual page content.

The body of the TJ article mentions this explicitly as a limitation, yet
`page_start` and `page_end` values in the published entity files are derived
from this approximation. For a 107-page Atlantic Area Supplement with 8,248 words,
each chunk is 77 words, and article boundaries could be misattributed by
10 to 15 pages.

**Impact on downstream extraction:** All `page_start` and `page_end` values are
unreliable. Any downstream use of layout-grounded page references (table of
contents generation, citation, section pairing) will be incorrect.

### Gap 4: Section Title Lookahead Is Missing

`SECTION_PAT` captures only text on the same line as the `SECTION N` token.
In many documents the heading reads `SECTION 1\n` on one line and the title
(`"Opening, Closing or Partial Closing"`) on the following line. The extractor
produces `"Section 1: Section 1"` for these cases. A review of the Atlantic
Area entities file reveals 6 sections with auto-generated fallback titles even
though the real titles appear one line below in the source text.

### Gap 5: Date Extraction Uses Position Instead of Semantics

`_infer_dates` returns the first and second date matches from the first 6,000
characters. In contracts where an expiration date appears in the preamble before
the effective date, the output is swapped. The Central Region Supplement entity
file shows `effective_date: "July 31, 2028"` and `expiration_date: "August 1,
2023"`, where the effective date is actually August 1, 2023. The extractor has no
pattern for labeled date contexts like `"effective August 1, 2023"` or
`"expiring July 31, 2028"`.

### Gap 6: Topic Detection Is Binary and Undifferentiated

`_detect_topics` produces a document-level list of present topics derived from
simple `in` checks against lowercased full text. Three issues follow. First, all
topics are weighted equally regardless of how many times or in how much depth
each appears. Second, a document with a single incidental mention of "overtime"
receives the same `overtime` tag as a document with a dedicated 10-section
article on overtime policy. Third, topics are not granular to article or section,
so the corpus cannot tell a synthetic generator which clauses belong to which
topic.

### Gap 7: Value-Bearing Parameters Are Entirely Absent

The most critical gap for synthetic data generation is that no monetary values,
percentages, time durations, or wage progression data are extracted at all.
Labor contracts are structurally defined by these exact parameters. The current
schema captures the shape of a contract (how many articles, which topics) but not
the substance (what wages, which rates, how many days for a grievance step).

A synthetic generator trained solely on the current output can reproduce the
skeleton of a contract but cannot produce any clause with a defensible number.
Wage tables, benefit contribution rates, vacation accrual schedules, probationary
periods, and grievance step timelines are the parameters that make a synthetic
contract plausible to a domain expert.

### Gap 8: No Corpus-Level Statistical Distributions

`corpus_summary.json` contains counts and lists but no distributions. A synthetic
generator needs to know not just that the average supplement has 21 articles, but
that article counts follow a distribution with mean 21, standard deviation 8,
and 5th/95th percentiles of 8 and 39. Without that distribution, a generator
cannot sample structurally realistic documents.

The same applies to the within-document distribution of words per section, the
cross-document co-occurrence matrix of topics, and the linguistic properties
(sentence length distribution, passive voice ratio, conditional clause density)
that determine whether generated text reads plausibly as legal prose.

---

## Proposed Extraction Plan v2

The plan is organized into seven phases. Phases 1 through 4 replace the extraction
core. Phases 5 and 6 add the value-bearing parameter and statistical layers that
are missing entirely. Phase 7 replaces the current ad-hoc QA scripts with a
systematic validation gate.

The plan assumes Python 3.11+ and introduces two required dependencies:
`pdfminer.six` for layout-aware text extraction and `pytesseract` (optional, for
the OCR fallback path on image-based PDFs). All other processing remains in
stdlib. A `requirements.txt` addition is specified in Phase 1.

### Phase 1: Layered PDF Extraction with pdfminer.six

Replace `extract_text_from_pdf` with a three-tier extraction strategy that
attempts each tier in order and falls back to the next on failure.

**Tier 1: pdfminer.six layout analysis**

Use `pdfminer.high_level.extract_pages` with `LAParams` to receive a stream of
`LTPage` objects. For each page, collect `LTTextLine` objects with their bounding
box coordinates and the dominant font size and font name of the characters they
contain.

```python
from pdfminer.high_level import extract_pages
from pdfminer.layout import LAParams, LTTextLine, LTChar, LTAnno

laparams = LAParams(line_margin=0.3, char_margin=2.0, word_margin=0.1)

for page_num, page_layout in enumerate(extract_pages(pdf_path, laparams=laparams), 1):
    for element in page_layout:
        if isinstance(element, LTTextLine):
            text = element.get_text().strip()
            font_sizes = [
                c.size for c in element if isinstance(c, LTChar)
            ]
            dominant_size = max(set(font_sizes), key=font_sizes.count) if font_sizes else 0
            is_bold = any(
                "Bold" in c.fontname or "bold" in c.fontname
                for c in element if isinstance(c, LTChar)
            )
            yield PageLine(
                text=text,
                page=page_num,
                font_size=dominant_size,
                is_bold=is_bold,
                x0=element.x0,
                y0=element.y0,
            )
```

pdfminer.six handles ToUnicode CMap resolution natively, which eliminates Gap 2.
It also resolves TJ array kerning at the character level before producing
`LTTextLine` output, which eliminates Gap 1. The per-page iteration eliminates
Gap 3.

**Tier 2: pdfminer.six simple extraction**

If Tier 1 produces fewer than 50 characters on more than 50% of pages (a sign
that the PDF uses Type3 or other uncommon font encodings), fall back to
`pdfminer.high_level.extract_text` per page. This is less precise but handles
edge-case encodings that confuse the layout analyzer.

**Tier 3: OCR via pdf2image + pytesseract**

If both pdfminer tiers produce fewer than 25 words per page, the document is
either image-based or encrypted. Convert pages to images at 300 DPI using
`pdf2image.convert_from_path` and run `pytesseract.image_to_string` on each.
OCR output will not carry font metadata, so heading detection in Phase 2 falls
back to pattern-only mode for these documents. Flag the document with
`"extraction_method": "ocr"` in its metadata.

**TJ kerning repair (retrofit for existing stdlib extractions)**

For documents already processed correctly by the existing extractor,
post-apply a kerning repair pass to `_parse_content_stream`:

```python
def _repair_tj_spacing(parts: list[str], kerning_values: list[float]) -> str:
    """
    Join TJ array fragments using kerning displacement to decide
    whether to insert a space between adjacent fragments.
    Threshold +250 text units is the standard inter-word gap indicator.
    """
    result = []
    for i, part in enumerate(parts):
        result.append(part)
        if i < len(kerning_values):
            if kerning_values[i] < -250:
                result.append(" ")
    return "".join(result)
```

Parsing the numeric values interleaved in TJ arrays (e.g. `[(Ab) -120 (cd)]`)
requires reading the number tokens between string tokens. This is a targeted
change to `_parse_content_stream` that does not affect the rest of the extractor.

### Phase 2: Font-Metric Heading Detection with Pattern Validation

Replace the pattern-first heading detection in `_extract_articles_sections` with
a two-pass font-size-ranked approach. This ensures headings are correctly
identified even when the ARTICLE/SECTION label is absent (e.g., Roman numeral
headings in some supplements) or when the encoding is imperfect.

**Pass 1: Rank font sizes across the document**

Collect all observed `(font_size, is_bold)` combinations and rank them by
descending size. Assign heading levels as follows:

```
largest size  → level 1  (document title, appears once)
second size   → level 2  (article headings)
third size    → level 3  (section headings)
body size     → body text (word count accumulation)
```

If fewer than three distinct font sizes are found, fall back to pattern-only
detection to handle flat-formatted documents.

**Pass 2: Pattern validation on fontsize-flagged lines**

Apply `ARTICLE_PAT` and `SECTION_PAT` only to lines already classified as
level-2 or level-3 by Pass 1. On a level-2 line that matches, extract
`article_number` from the pattern. If `group(2)` (the title) is empty or a
single word, perform a one-line lookahead: consume the next non-blank body line
as the article title.

```python
def _extract_heading_title(line: PageLine, next_line: PageLine | None) -> str:
    """Return title from current line, or lookahead if current title is empty."""
    m = ARTICLE_PAT.match(line.text)
    if m:
        title = m.group(2).strip()
        if (not title or len(title.split()) <= 1) and next_line:
            if next_line.font_size <= line.font_size:
                return next_line.text.strip()[:200]
        return title or f"Article {m.group(1)}"
    return line.text.strip()[:200]
```

This directly resolves Gap 4.

### Phase 3: Semantic Date and Party Extraction

Replace `_infer_dates` with a context-labeled date extractor that requires a
semantic trigger word within 12 tokens of the date match.

```python
EFF_PAT = re.compile(
    r'(?:effective|commencing|beginning|shall\s+commence)\s+(?:as\s+of\s+)?'
    r'((?:January|February|March|April|May|June|July|August|'
    r'September|October|November|December)\s+\d{1,2},?\s+\d{4})',
    re.IGNORECASE
)
EXP_PAT = re.compile(
    r'(?:expir(?:e|es|ation|ing)|terminat(?:e|es|ion)|through|until|'
    r'ending|conclud(?:e|es|ing))\s+(?:on\s+)?'
    r'((?:January|February|March|April|May|June|July|August|'
    r'September|October|November|December)\s+\d{1,2},?\s+\d{4})',
    re.IGNORECASE
)
```

Search the first 8,000 characters for each labeled pattern. Only fall back to
positional date extraction if neither labeled pattern matches. Additionally,
compute `contract_duration_days` as an integer:

```python
from datetime import datetime

def _compute_duration(eff: str | None, exp: str | None) -> int | None:
    fmt_candidates = ["%B %d, %Y", "%B %d %Y", "%B %Y"]
    for fmt in fmt_candidates:
        try:
            e = datetime.strptime(eff, fmt)
            x = datetime.strptime(exp, fmt)
            return (x - e).days
        except (ValueError, TypeError):
            pass
    return None
```

A corpus-level check that `contract_duration_days` clusters tightly near 1826
(5 years) serves as a validation signal: outliers indicate extraction errors.

### Phase 4: Value-Bearing Parameter Extraction

This phase adds three new top-level arrays to every entity file. These fields
contain the parameters that a synthetic data generator needs to produce
numerically plausible contract clauses.

**4.1 Wage parameters**

```python
WAGE_PATTERNS = [
    # Explicit hourly rates
    re.compile(
        r'\$\s*(?P<value>\d{1,3}(?:\.\d{2})?)\s*(?:per\s+hour|/\s*hr\.?)',
        re.IGNORECASE
    ),
    # Progression table rows: Start / 12 months / 24 months / Top Rate
    re.compile(
        r'(?P<step>start|seniority|after\s+\d+\s+(?:months?|years?))'
        r'\s*[:\-]?\s*\$\s*(?P<value>\d{1,3}(?:\.\d{2})?)',
        re.IGNORECASE
    ),
    # General wage increase percentage
    re.compile(
        r'(?:general\s+)?wage\s+(?:increase|adjustment)\s+of\s+'
        r'(?P<value>\d+(?:\.\d+)?)\s*(?:percent|%)',
        re.IGNORECASE
    ),
]
```

Each match produces a record:

```json
{
  "type": "hourly_rate",
  "value": 21.77,
  "currency": "USD",
  "unit": "per_hour",
  "classification": "package_driver",
  "progression_step": "start",
  "article": "34",
  "section": "1",
  "page": 12
}
```

Classification context (e.g., `"package_driver"`, `"feeder_driver"`,
`"part_time"`) is inferred from the surrounding sentence using a keyword window
of 30 characters to the left of the matched value.

**4.2 Time duration parameters**

```python
DURATION_PATTERNS = [
    # Probationary and qualifying periods
    re.compile(
        r'(?P<context>probationary|qualifying|introductory)\s+period'
        r'\s+of\s+(?P<value>\d+)\s+(?P<unit>working\s+days?|days?|weeks?|months?)',
        re.IGNORECASE
    ),
    # Grievance step time limits
    re.compile(
        r'within\s+(?P<value>\d+)\s+(?P<unit>working\s+days?|calendar\s+days?|days?|hours?)',
        re.IGNORECASE
    ),
    # Notice periods
    re.compile(
        r'(?P<value>\d+)\s+(?P<unit>days?|weeks?)\s+(?:advance\s+)?notice',
        re.IGNORECASE
    ),
]
```

Each match produces a record:

```json
{
  "type": "probationary_period",
  "value": 30,
  "unit": "working_days",
  "article": "46",
  "section": "1",
  "page": 4
}
```

**4.3 Benefit and pension contribution parameters**

```python
BENEFIT_PATTERNS = [
    # Weekly / monthly monetary contributions
    re.compile(
        r'\$\s*(?P<value>\d{1,4}(?:\.\d{2})?)'
        r'\s*per\s+(?P<unit>week|month|year|employee)',
        re.IGNORECASE
    ),
    # Pension contribution rates as percentages
    re.compile(
        r'pension\s+contribution\s+of\s+'
        r'(?P<value>\d+(?:\.\d+)?)\s*(?:percent|%)',
        re.IGNORECASE
    ),
]
```

**4.4 Cross-reference graph**

Parse inline cross-references to build a directed adjacency list:

```python
XREF_PAT = re.compile(
    r'(?:pursuant\s+to|as\s+(?:set\s+forth|defined)\s+in|under|'
    r'referred\s+to\s+in|in\s+accordance\s+with)\s+'
    r'Article\s+(\d+[a-zA-Z]?)'
    r'(?:,?\s+Section\s+(\d+))?',
    re.IGNORECASE
)
```

The output is an array of `{"from_article", "from_section", "to_article",
"to_section"}` objects. This constrains which articles a synthetic generator
must produce together to maintain referential integrity.

### Phase 5: Corpus-Level Statistical Aggregation

Extend `corpus_summary.json` with a `statistics` block computed after all
entity files are written.

**5.1 Structural distributions**

For each numeric field across all documents, compute `{mean, std, min, p10,
p25, p50, p75, p90, max}`:

```python
from statistics import mean, stdev
import numpy as np  # or use stdlib math for percentiles via sorted list

def distribution(values: list[float]) -> dict:
    s = sorted(values)
    n = len(s)
    return {
        "mean":  round(mean(s), 2),
        "std":   round(stdev(s), 2) if n > 1 else 0.0,
        "min":   s[0],
        "p10":   s[max(0, int(n * 0.10))],
        "p25":   s[max(0, int(n * 0.25))],
        "p50":   s[n // 2],
        "p75":   s[min(n-1, int(n * 0.75))],
        "p90":   s[min(n-1, int(n * 0.90))],
        "max":   s[-1],
    }
```

Compute distributions for:

- `articles_per_document` (by document type separately)
- `sections_per_article`
- `words_per_section`
- `words_per_article`
- `pages_per_document`
- `contract_duration_days`
- `wage_values` (all extracted hourly rates)
- `duration_values_days` (all time parameter values normalized to days)

**5.2 Topic co-occurrence matrix**

The co-occurrence count for each pair of topics across the corpus drives
topic-pairing constraints in synthetic generation:

```python
from itertools import combinations
from collections import Counter

co_matrix: dict[str, dict[str, int]] = {}
for doc in all_entities:
    topics = doc["key_topics"]
    for a, b in combinations(sorted(topics), 2):
        co_matrix.setdefault(a, {}).setdefault(b, 0)
        co_matrix[a][b] += 1
```

Include this matrix in `corpus_summary.json` as `"topic_co_occurrence"`. A
synthetic generator can use this to reject samples where topic combinations
never appear in the real corpus.

**5.3 Linguistic metrics**

Compute per-document and corpus-aggregate:

```python
import re as _re

def sentence_stats(text: str) -> dict:
    sentences = _re.split(r'(?<=[.!?])\s+', text.strip())
    lengths = [len(s.split()) for s in sentences if len(s.split()) >= 3]
    return {
        "mean_sentence_words":   round(mean(lengths), 1) if lengths else 0,
        "std_sentence_words":    round(stdev(lengths), 1) if len(lengths) > 1 else 0,
        "conditional_density":   sum(
            1 for s in sentences
            if _re.search(
                r'\b(if|provided|notwithstanding|in the event|subject to)\b',
                s, _re.IGNORECASE
            )
        ) / max(len(sentences), 1),
        "passive_ratio":         sum(
            1 for s in sentences
            if _re.search(r'\b(?:is|are|was|were|be|been|being)\s+\w+ed\b', s)
        ) / max(len(sentences), 1),
    }
```

**5.4 Boilerplate density**

Compute per-section TF-IDF scores. Sections whose terms have near-zero IDF
(appearing in more than 80% of documents) are boilerplate. Store the boilerplate
ratio per section. Synthetic generators can use this to distinguish jurisdiction-
specific language from standard carryover text.

### Phase 6: Updated JSON Schema

Add the following top-level fields to `CONTRACT_ENTITY_SCHEMA` (v2):

```json
"wage_parameters": {
  "type": "array",
  "items": {
    "type": "object",
    "required": ["type", "value", "unit"],
    "properties": {
      "type":              {"type": "string"},
      "value":             {"type": "number"},
      "currency":          {"type": "string"},
      "unit":              {"type": "string"},
      "classification":    {"type": ["string", "null"]},
      "progression_step":  {"type": ["string", "null"]},
      "article":           {"type": ["string", "null"]},
      "section":           {"type": ["string", "null"]},
      "page":              {"type": ["integer", "null"]}
    }
  }
},
"time_parameters": {
  "type": "array",
  "items": {
    "type": "object",
    "required": ["type", "value", "unit"],
    "properties": {
      "type":     {"type": "string"},
      "value":    {"type": "number"},
      "unit":     {"type": "string"},
      "article":  {"type": ["string", "null"]},
      "section":  {"type": ["string", "null"]},
      "page":     {"type": ["integer", "null"]}
    }
  }
},
"benefit_parameters": {
  "type": "array",
  "items": {
    "type": "object",
    "required": ["type", "value", "unit"],
    "properties": {
      "type":     {"type": "string"},
      "value":    {"type": "number"},
      "currency": {"type": ["string", "null"]},
      "unit":     {"type": "string"},
      "article":  {"type": ["string", "null"]},
      "section":  {"type": ["string", "null"]},
      "page":     {"type": ["integer", "null"]}
    }
  }
},
"cross_references": {
  "type": "array",
  "items": {
    "type": "object",
    "required": ["from_article", "to_article"],
    "properties": {
      "from_article":  {"type": ["string", "null"]},
      "from_section":  {"type": ["string", "null"]},
      "to_article":    {"type": "string"},
      "to_section":    {"type": ["string", "null"]}
    }
  }
},
"contract_duration_days": {"type": ["integer", "null"]},
"extraction_method":      {"type": "string", "enum": ["pdfminer", "pdfminer_simple", "ocr", "stdlib_fallback"]}
```

Bump `SCHEMA_VERSION` to `"v2"` and `EXTRACTOR_VERSION` to `"2.0.0"`.

### Phase 7: Systematic QA Gates

Replace the individual diagnostic scripts with a single `validate_corpus.py`
that runs all checks in a structured pipeline and exits with a non-zero code
when any gate fails.

#### Gate 1: Structural completeness

For every document with `page_count >= 10` and `extraction_method != "ocr"`:

- `articles >= 1` (at least one article identified)
- `words_per_page >= 50` (not sparse)
- No header text matching `GARBLED_PAT` (control characters or Windows-1252
  junk bytes)
- No section title matching `"Section N: Section N"` (auto-generated fallback)

#### Gate 2: Date integrity

For every document with non-null `effective_date` and `expiration_date`:

- `contract_duration_days > 0` (expiration is after effective)
- `contract_duration_days` falls within 2 standard deviations of the corpus mean
  (validates that labeled date extraction matches the known contract cycle)

#### Gate 3: Value parameter coverage

For every document with topics including `"wages"`:

- `len(wage_parameters) >= 1` (at least one wage value extracted)

For every document with topics including `"grievance_arbitration"`:

- `len(time_parameters) >= 1` (at least one time limit extracted)

#### Gate 4: Statistical sanity

After corpus aggregation:

- `topic_co_occurrence["wages"]["health_and_welfare"] >= 25` (these always
  co-occur in the real corpus; a value below 25 indicates a topic-detection
  regression)
- `statistics.articles_per_document.std < statistics.articles_per_document.mean`
  (coefficient of variation check; if std exceeds mean, article detection has
  collapsed)

---

## Implementation Order and Dependencies

| Phase | New file or change | Blocking dependency |
|---|---|---|
| 1 | `pdf_extractor_v2.py` (new) replaces `extract_text_from_pdf` | `pip install pdfminer.six` |
| 2 | `_extract_headings_v2` in same file | Phase 1 output |
| 3 | `_infer_dates_v2` replacing `_infer_dates` | Phase 1 output |
| 4 | `_extract_values.py` (new) | Phase 1 output |
| 5 | `build_corpus_stats.py` (new) | Phase 4 complete |
| 6 | `contract_entity_schema_v2.json` (new) | Phases 4 and 5 |
| 7 | `validate_corpus.py` replacing all `diagnose_*.py` | Phase 6 schema |

The Phase 1 extraction rewrite is the critical path. Phases 2 through 4 consume
its output and can be developed in parallel once Phase 1 is stable on a sample of
five documents (one Master Agreement, two Supplements, one Rider, one CID-font
document).

> [!NOTE]
> The OCR path in Tier 3 requires `poppler` system binaries in addition to
> `pdf2image`. On Windows, place `poppler/bin` on `PATH` before running the
> extractor. The OCR path is only triggered for documents that Tiers 1 and 2
> cannot decode. Current corpus analysis shows 0 confirmed image-based PDFs,
> so the OCR path is a resilience measure rather than a first-class use case.

> [!TIP]
> Run Phase 1 against the Latin America Rider and the Puerto Rico Supplement
> first. These two documents represent the two hardest encoding cases in the
> corpus (CID fonts, InDesign kerning). If Phase 1 extracts clean text from both,
> all other documents will follow.
