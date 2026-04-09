"""
_extract_values.py — Value-bearing parameter extraction for UPS labor contracts.
Provides regex-based extraction of wages, time parameters, benefits, and cross-references.
pdfplumber table extraction is guarded and degrades gracefully when pdfplumber is absent.
"""
from __future__ import annotations
import re
from typing import Optional

try:
    import pdfplumber  # Optional: for wage table extraction
    _HAS_PDFPLUMBER = True
except ImportError:
    _HAS_PDFPLUMBER = False

# ── Wage Patterns ─────────────────────────────────────────────────────────────

WAGE_DOLLAR = re.compile(
    r'\$(\d{1,3}(?:\.\d{2})?)\s*(?:per\s+(?:hour|hr|week|month)|/(?:hr|hour|wk|mo))?',
    re.IGNORECASE
)

WAGE_LABELED = re.compile(
    r'((?:Package|Feeder|Air|Mechanic|Combo|Part[ -]Time|Full[ -]Time|'
    r'Driver|Inside|Sorter|Preloader|Cleaner|Car\s*Wash|Utility)[^:$\n]{0,40})'
    r'[:\s]+\$(\d{1,3}(?:\.\d{2})?)',
    re.IGNORECASE
)

WAGE_HEADING_PAT = re.compile(
    r'(?:wage\s+schedule|progression\s+table|classification\s+and\s+rates?'
    r'|wage\s+progression|full.time\s+wage|part.time\s+wage'
    r'|wage\s+increase|hourly\s+rates?)',
    re.IGNORECASE
)

STEP_LABELS: dict[str, str] = {
    r'start(?:ing\s+rate)?':                          'start',
    r'(?:after\s+)?seniority':                        'seniority',
    r'(?:after\s+)?(?:12|twelve)\s+months?':          '12_months',
    r'(?:after\s+)?(?:1|one)\s+year':                 '12_months',
    r'(?:after\s+)?(?:24|twenty.?four)\s+months?':    '24_months',
    r'(?:after\s+)?(?:2|two)\s+years?':               '24_months',
    r'(?:after\s+)?(?:36|thirty.?six)\s+months?':     '36_months',
    r'(?:after\s+)?(?:3|three)\s+years?':             '36_months',
    r'top\s+(?:rate|scale)':                          'top_rate',
}

_STEP_COMPILED = [(re.compile(pat, re.IGNORECASE), label)
                  for pat, label in STEP_LABELS.items()]

# ── Time Patterns ─────────────────────────────────────────────────────────────

PROB_PAT = re.compile(
    r'(\d+)\s+(working\s+days?|calendar\s+days?|days?|weeks?|months?)'
    r'(?:\s+(?:probationary|qualifying|trial|introductory)\s+period)?'
    r'|(?:probationary|qualifying)\s+period\s+of\s+(\d+)\s+(working\s+days?|calendar\s+days?|days?|weeks?|months?)',
    re.IGNORECASE
)

GRIEVANCE_PAT = re.compile(
    r'within\s+(\d+)\s+(working\s+days?|calendar\s+days?|business\s+days?|days?)',
    re.IGNORECASE
)

NOTICE_PAT = re.compile(
    r'(\d+)[\s-]+(?:days?|weeks?)\s+(?:advance\s+)?notice',
    re.IGNORECASE
)

# ── Benefit Patterns ──────────────────────────────────────────────────────────

BENEFIT_DOLLAR_PAT = re.compile(
    r'\$(\d{1,4}(?:,\d{3})?(?:\.\d{2})?)\s*per\s+(week|month)',
    re.IGNORECASE
)

PENSION_PCT_PAT = re.compile(
    r'(\d+(?:\.\d+)?)\s*(?:%|percent)\s+(?:of\s+(?:gross|earnings|wages?))',
    re.IGNORECASE
)

# ── Cross-Reference Pattern ───────────────────────────────────────────────────

XREF_PAT = re.compile(
    r'(?:Article|Section)\s+(\d+|[IVXLCDM]+)\s+(?:of\s+this\s+)?(?:Agreement|Supplement|Rider|Contract)',
    re.IGNORECASE
)


# ─────────────────────────────────────────────────────────────────────────────
# Helper: classify progression step from surrounding context
# ─────────────────────────────────────────────────────────────────────────────

def _classify_step(context: str) -> str:
    for pat, label in _STEP_COMPILED:
        if pat.search(context):
            return label
    return "top_rate"


# ─────────────────────────────────────────────────────────────────────────────
# Helper: normalize unit string
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_unit(raw: str) -> str:
    raw = raw.lower().strip()
    if raw.startswith('hour') or raw in ('hr', '/hr', '/hour'):
        return 'hour'
    if raw.startswith('week') or raw in ('wk', '/wk'):
        return 'week'
    if raw.startswith('month') or raw in ('mo', '/mo'):
        return 'month'
    if raw.startswith('year'):
        return 'year'
    return 'hour'


# ─────────────────────────────────────────────────────────────────────────────
# 1. extract_wage_parameters
# ─────────────────────────────────────────────────────────────────────────────

def extract_wage_parameters(
    full_text: str,
    sections: Optional[list] = None,
) -> list[dict]:
    """
    Extract wage/rate records from full contract text.

    Returns up to 50 unique records (deduped by classification+value).
    Wages outside [$8.00, $60.00] are filtered out.
    """
    records: list[dict] = []
    seen: set[tuple] = set()

    # Phase 1: labeled wages (classification context present)
    for m in WAGE_LABELED.finditer(full_text):
        classification = m.group(1).strip().rstrip(':').strip()
        try:
            value = float(m.group(2))
        except (TypeError, ValueError):
            continue

        if not (8.00 <= value <= 60.00):
            continue

        # Look for progression step in surrounding context (50 chars before match)
        start = max(0, m.start() - 50)
        ctx = full_text[start: m.end() + 50]
        step = _classify_step(ctx)

        key = (classification.lower()[:40], value)
        if key in seen:
            continue
        seen.add(key)

        records.append({
            "type":             "hourly",
            "value":            value,
            "currency":         "USD",
            "unit":             "hour",
            "classification":   classification,
            "progression_step": step,
            "article":          None,
            "section":          None,
            "page":             None,
        })

        if len(records) >= 50:
            return records

    # Phase 2: wage-schedule context regions (standalone dollar amounts)
    for heading_m in WAGE_HEADING_PAT.finditer(full_text):
        region_start = heading_m.start()
        region_end   = min(len(full_text), region_start + 2000)
        region_text  = full_text[region_start:region_end]

        for dm in WAGE_DOLLAR.finditer(region_text):
            try:
                value = float(dm.group(1))
            except (TypeError, ValueError):
                continue

            if not (8.00 <= value <= 60.00):
                continue

            ctx_start = max(0, dm.start() - 50)
            ctx = region_text[ctx_start: dm.end() + 50]
            step = _classify_step(ctx)

            key = ("schedule_rate", value)
            if key in seen:
                continue
            seen.add(key)

            records.append({
                "type":             "hourly",
                "value":            value,
                "currency":         "USD",
                "unit":             "hour",
                "classification":   "schedule_rate",
                "progression_step": step,
                "article":          None,
                "section":          None,
                "page":             None,
            })

            if len(records) >= 50:
                return records

    return records


# ─────────────────────────────────────────────────────────────────────────────
# 2. extract_time_parameters
# ─────────────────────────────────────────────────────────────────────────────

_GRIEVANCE_KEYWORDS = re.compile(
    r'grievance|arbitration|appeal|protest', re.IGNORECASE
)
_PROBATIONARY_KEYWORDS = re.compile(
    r'probationary|qualifying|trial\s+period|introductory', re.IGNORECASE
)
_NOTICE_KEYWORDS = re.compile(
    r'advance\s+notice|prior\s+notice|written\s+notice', re.IGNORECASE
)


def _normalize_time_unit(raw: str) -> str:
    raw = raw.lower().strip()
    if 'working' in raw and 'day' in raw:
        return 'working_days'
    if 'calendar' in raw and 'day' in raw:
        return 'calendar_days'
    if 'business' in raw and 'day' in raw:
        return 'calendar_days'
    if 'day' in raw:
        return 'days'
    if 'week' in raw:
        return 'weeks'
    if 'month' in raw:
        return 'months'
    return 'days'


def _classify_time_type(context: str) -> str:
    if _GRIEVANCE_KEYWORDS.search(context):
        return 'grievance_step_limit'
    if _PROBATIONARY_KEYWORDS.search(context):
        return 'probationary_period'
    if _NOTICE_KEYWORDS.search(context):
        return 'notice_period'
    return 'waiting_period'


def extract_time_parameters(
    full_text: str,
    sections: Optional[list] = None,
) -> list[dict]:
    """
    Extract time/duration parameter records from full contract text.

    Returns up to 30 records.
    """
    records: list[dict] = []
    seen: set[tuple] = set()

    # Probationary / qualifying matches
    for m in PROB_PAT.finditer(full_text):
        if m.group(1) is not None:
            try:
                value = int(m.group(1))
            except (TypeError, ValueError):
                continue
            unit_raw = m.group(2)
        else:
            try:
                value = int(m.group(3))
            except (TypeError, ValueError):
                continue
            unit_raw = m.group(4)

        unit = _normalize_time_unit(unit_raw or '')
        ctx_start = max(0, m.start() - 100)
        ctx = full_text[ctx_start: m.end() + 100]
        param_type = _classify_time_type(ctx)
        description = full_text[max(0, m.start() - 10): m.end() + 10].strip()

        key = (param_type, value, unit)
        if key in seen:
            continue
        seen.add(key)

        records.append({
            "type":        param_type,
            "value":       value,
            "unit":        unit,
            "description": description,
            "article":     None,
            "section":     None,
            "page":        None,
        })

        if len(records) >= 30:
            return records

    # Grievance time limits
    for m in GRIEVANCE_PAT.finditer(full_text):
        try:
            value = int(m.group(1))
        except (TypeError, ValueError):
            continue
        unit = _normalize_time_unit(m.group(2))
        ctx_start = max(0, m.start() - 100)
        ctx = full_text[ctx_start: m.end() + 100]
        description = full_text[max(0, m.start() - 10): m.end() + 10].strip()

        key = ('grievance_step_limit', value, unit)
        if key in seen:
            continue
        seen.add(key)

        records.append({
            "type":        "grievance_step_limit",
            "value":       value,
            "unit":        unit,
            "description": description,
            "article":     None,
            "section":     None,
            "page":        None,
        })

        if len(records) >= 30:
            return records

    # Notice periods
    for m in NOTICE_PAT.finditer(full_text):
        try:
            value = int(m.group(1))
        except (TypeError, ValueError):
            continue
        raw_match = m.group(0).lower()
        unit = 'weeks' if 'week' in raw_match else 'days'
        description = full_text[max(0, m.start() - 10): m.end() + 10].strip()

        key = ('notice_period', value, unit)
        if key in seen:
            continue
        seen.add(key)

        records.append({
            "type":        "notice_period",
            "value":       value,
            "unit":        unit,
            "description": description,
            "article":     None,
            "section":     None,
            "page":        None,
        })

        if len(records) >= 30:
            return records

    return records


# ─────────────────────────────────────────────────────────────────────────────
# 3. extract_benefit_parameters
# ─────────────────────────────────────────────────────────────────────────────

_HEALTH_KEYWORDS = re.compile(
    r'health\s+(?:and\s+)?welfare|medical|dental|vision|h&w', re.IGNORECASE
)
_PENSION_KEYWORDS = re.compile(
    r'pension|retirement|annuity', re.IGNORECASE
)
_VACATION_KEYWORDS = re.compile(
    r'vacation|holiday', re.IGNORECASE
)


def _classify_benefit_type(context: str) -> str:
    if _PENSION_KEYWORDS.search(context):
        return 'pension_contribution'
    if _HEALTH_KEYWORDS.search(context):
        return 'health_welfare_contribution'
    if _VACATION_KEYWORDS.search(context):
        return 'vacation_pay'
    return 'health_welfare_contribution'


def extract_benefit_parameters(
    full_text: str,
    sections: Optional[list] = None,
) -> list[dict]:
    """
    Extract benefit/contribution parameter records from full contract text.

    Returns up to 20 records.
    """
    records: list[dict] = []
    seen: set[tuple] = set()

    # Weekly / monthly monetary contributions
    for m in BENEFIT_DOLLAR_PAT.finditer(full_text):
        raw_val = m.group(1).replace(',', '')
        try:
            value = float(raw_val)
        except (TypeError, ValueError):
            continue

        unit_raw = m.group(2).lower()
        unit = 'week' if 'week' in unit_raw else 'month'

        ctx_start = max(0, m.start() - 100)
        ctx = full_text[ctx_start: m.end() + 50]
        benefit_type = _classify_benefit_type(ctx)
        description = full_text[max(0, m.start() - 10): m.end() + 10].strip()

        key = (benefit_type, value, unit)
        if key in seen:
            continue
        seen.add(key)

        records.append({
            "type":        benefit_type,
            "value":       value,
            "currency":    "USD",
            "unit":        unit,
            "description": description,
            "article":     None,
            "section":     None,
            "page":        None,
        })

        if len(records) >= 20:
            return records

    # Pension percentage rates
    for m in PENSION_PCT_PAT.finditer(full_text):
        try:
            value = float(m.group(1))
        except (TypeError, ValueError):
            continue

        description = full_text[max(0, m.start() - 10): m.end() + 10].strip()
        key = ('pension_contribution', value, 'percent')
        if key in seen:
            continue
        seen.add(key)

        records.append({
            "type":        "pension_contribution",
            "value":       value,
            "currency":    "USD",
            "unit":        "percent",
            "description": description,
            "article":     None,
            "section":     None,
            "page":        None,
        })

        if len(records) >= 20:
            return records

    return records


# ─────────────────────────────────────────────────────────────────────────────
# 4. extract_cross_references
# ─────────────────────────────────────────────────────────────────────────────

def extract_cross_references(
    full_text: str,
    articles: Optional[list] = None,
) -> list[dict]:
    """
    Extract inline cross-references between articles/sections.

    Returns up to 20 unique cross-reference records.
    """
    records: list[dict] = []
    seen: set[tuple] = set()

    for m in XREF_PAT.finditer(full_text):
        to_article = m.group(1)
        key = (None, None, to_article, None)
        if key in seen:
            continue
        seen.add(key)

        records.append({
            "from_article":  None,
            "from_section":  None,
            "to_article":    to_article,
            "to_section":    None,
        })

        if len(records) >= 20:
            break

    return records
