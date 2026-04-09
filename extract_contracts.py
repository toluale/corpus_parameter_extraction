"""
UPS Labor Contract Entity Extractor
====================================
Pure-stdlib PDF text extraction + entity extraction + JSON Schema generation.
Requires only Python 3.11+ (uses built-in re, zlib, json, hashlib, pathlib).

Outputs
-------
json_schemas/
    contract_entity_schema_definition.json   ← JSON Schema (Draft-7)
    corpus_summary.json                       ← Corpus-level aggregate
    entities/
        <name>_entities.json                  ← One file per PDF
"""

from __future__ import annotations
import re, zlib, json, hashlib, struct, math
import statistics as _stat
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter
from itertools import combinations
from _extract_values import (
    extract_wage_parameters,
    extract_time_parameters,
    extract_benefit_parameters,
    extract_cross_references,
)
try:
    from pdf_extractor_v2 import extract_text_pages as _v2_extract
    _HAS_V2_EXTRACTOR = True
except Exception:
    _HAS_V2_EXTRACTOR = False

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR   = Path(r"c:\Users\SKP0MRS\Documents\UPS\UPS_Labor_Contracts_1")
OUTPUT_DIR = BASE_DIR / "json_schemas"
ENTITY_DIR = OUTPUT_DIR / "entities"
OUTPUT_DIR.mkdir(exist_ok=True)
ENTITY_DIR.mkdir(exist_ok=True)

PDF_FILES  = sorted(BASE_DIR.glob("*.pdf"))

EXTRACTOR_VERSION = "2.1.0"
SCHEMA_VERSION    = "v2"


# ══════════════════════════════════════════════════════════════════════════════
# PART 1 — JSON Schema Definition
# ══════════════════════════════════════════════════════════════════════════════

CONTRACT_ENTITY_SCHEMA: dict = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "$id": "ups-labor-contract-entity-schema-v2",
    "title": "UPS Labor Contract Entity Schema",
    "description": (
        "Schema for structured entities extracted from UPS labor contract PDFs "
        "for synthetic data generation while protecting sensitive and IID information."
    ),
    "type": "object",
    "required": [
        "document_id", "source_file", "document_type", "title",
        "parties", "effective_date", "page_count",
        "headers", "sections", "articles", "key_topics",
        "extraction_method", "extraction_metadata"
    ],
    "properties": {
        "document_id": {
            "type": "string",
            "description": "SHA-256 hash of the source filename (no PII).",
            "minLength": 16, "maxLength": 16
        },
        "source_file": {
            "type": "string",
            "description": "Original PDF filename."
        },
        "document_type": {
            "type": "string",
            "enum": [
                "National Master Agreement",
                "Supplemental Agreement",
                "Rider",
                "Addendum",
                "Other"
            ]
        },
        "title": {
            "type": "string",
            "description": "Human-readable contract title derived from content or filename.",
            "minLength": 1
        },
        "parties": {
            "type": "object",
            "required": ["employer", "union"],
            "properties": {
                "employer":          {"type": "string"},
                "union":             {"type": "string"},
                "local_number":      {"type": ["string", "null"]},
                "geographic_region": {"type": ["string", "null"]}
            },
            "additionalProperties": False
        },
        "effective_date":  {"type": ["string", "null"]},
        "expiration_date": {"type": ["string", "null"]},
        "contract_duration_days": {
            "type": ["integer", "null"],
            "description": "Number of days between effective_date and expiration_date. Null when either date is not extractable.",
            "minimum": 0
        },
        "page_count":      {"type": "integer", "minimum": 0},
        "word_count":      {"type": "integer", "minimum": 0},
        "headers": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["text", "page", "level"],
                "properties": {
                    "text":  {"type": "string"},
                    "page":  {"type": "integer", "minimum": 1},
                    "level": {"type": "integer", "enum": [1, 2, 3]}
                },
                "additionalProperties": False
            }
        },
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["section_id", "parent_article", "title", "page_start"],
                "properties": {
                    "section_id":     {"type": "string"},
                    "parent_article": {"type": ["string", "null"]},
                    "title":          {"type": "string"},
                    "page_start":     {"type": "integer", "minimum": 1},
                    "page_end":       {"type": ["integer", "null"]},
                    "word_count":     {"type": "integer", "minimum": 0}
                },
                "additionalProperties": False
            }
        },
        "articles": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["article_number", "title", "page_start"],
                "properties": {
                    "article_number": {"type": "string"},
                    "title":          {"type": "string"},
                    "page_start":     {"type": "integer", "minimum": 1},
                    "page_end":       {"type": ["integer", "null"]},
                    "section_count":  {"type": "integer", "minimum": 0},
                    "word_count":     {"type": "integer", "minimum": 0}
                },
                "additionalProperties": False
            }
        },
        "key_topics": {
            "type": "array",
            "uniqueItems": True,
            "items": {
                "type": "string",
                "enum": [
                    "wages", "benefits", "health_and_welfare", "pension",
                    "hours_of_work", "overtime", "seniority", "discipline",
                    "grievance_arbitration", "union_security", "management_rights",
                    "leaves_of_absence", "safety_and_health", "holidays",
                    "vacations", "job_classifications", "supplemental_work",
                    "new_technology", "subcontracting", "part_time_employees",
                    "full_time_employees", "feeders", "package_delivery",
                    "air_operations", "cartage", "mechanics",
                    "combination_employees", "general_provisions", "other"
                ]
            }
        },
        "wage_parameters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type":             {"type": "string"},
                    "value":            {"type": "number"},
                    "currency":         {"type": "string"},
                    "unit":             {"type": "string"},
                    "classification":   {"type": "string"},
                    "progression_step": {"type": "string"},
                    "article":          {"type": ["string", "null"]},
                    "section":          {"type": ["string", "null"]},
                    "page":             {"type": ["integer", "null"]}
                }
            }
        },
        "time_parameters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type":        {"type": "string"},
                    "value":       {"type": "number"},
                    "unit":        {"type": "string"},
                    "description": {"type": "string"},
                    "article":     {"type": ["string", "null"]},
                    "section":     {"type": ["string", "null"]},
                    "page":        {"type": ["integer", "null"]}
                }
            }
        },
        "benefit_parameters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type":        {"type": "string"},
                    "value":       {"type": "number"},
                    "currency":    {"type": "string"},
                    "unit":        {"type": "string"},
                    "description": {"type": "string"},
                    "article":     {"type": ["string", "null"]},
                    "section":     {"type": ["string", "null"]},
                    "page":        {"type": ["integer", "null"]}
                }
            }
        },
        "cross_references": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "from_article":  {"type": ["string", "null"]},
                    "from_section":  {"type": ["string", "null"]},
                    "to_article":    {"type": ["string", "null"]},
                    "to_section":    {"type": ["string", "null"]}
                }
            }
        },
        "extraction_method": {
            "type": "string",
            "enum": ["stdlib_pdf", "pdfminer", "pymupdf", "ocr", "pypdf-tier0", "pdfminer-tier1", "pdfminer-tier2", "pymupdf-tier3-cid", "pymupdf-tier3-fallback"],
            "description": "Library/method used to extract text from this PDF."
        },
        "extraction_metadata": {
            "type": "object",
            "required": ["extracted_at", "extractor_version", "schema_version"],
            "properties": {
                "extracted_at":      {"type": "string"},
                "extractor_version": {"type": "string"},
                "schema_version":    {"type": "string"},
                "extraction_notes":  {"type": "array", "items": {"type": "string"}}
            },
            "additionalProperties": False
        }
    },
    "additionalProperties": False
}


# ══════════════════════════════════════════════════════════════════════════════
# PART 2 — Pure-stdlib PDF Text Extractor
# ══════════════════════════════════════════════════════════════════════════════

def _decode_hex_pdf_string(hex_bytes: bytes, assume_utf16: bool = False) -> str:
    """Decode a hex-encoded PDF string <XXXX>.

    Handles:
    - UTF-16 BE with BOM (\\xFE\\xFF prefix)
    - UTF-16 LE with BOM (\\xFF\\xFE prefix)
    - Bare UTF-16 BE (when assume_utf16=True and every even byte is \\x00)
    - Single-byte Latin-1 fallback
    """
    try:
        raw = bytes.fromhex(hex_bytes.decode('ascii'))
    except Exception:
        return ''
    if raw[:2] == b'\xfe\xff':
        try:
            return raw[2:].decode('utf-16-be')
        except Exception:
            pass
    if raw[:2] == b'\xff\xfe':
        try:
            return raw[2:].decode('utf-16-le')
        except Exception:
            pass
    if assume_utf16 and len(raw) >= 2:
        try:
            text = raw.decode('utf-16-be')
            # Only accept if result is mostly printable ASCII/Latin-1 (not CID codes)
            printable = sum(1 for c in text if c.isprintable() and ord(c) < 0x10000)
            if printable / max(len(text), 1) > 0.5:
                return text
        except Exception:
            pass
    return raw.decode('latin-1', errors='replace')


def _decode_pdf_string(raw: bytes) -> str:
    """Decode a raw PDF string literal (handles backslash escapes + octal)."""
    result = bytearray()
    i = 0
    while i < len(raw):
        c = raw[i:i+1]
        if c == b'\\':
            i += 1
            if i >= len(raw):
                break
            esc = raw[i:i+1]
            if esc == b'n':   result.append(0x0A)
            elif esc == b'r': result.append(0x0D)
            elif esc == b't': result.append(0x09)
            elif esc == b'b': result.append(0x08)
            elif esc == b'f': result.append(0x0C)
            elif esc in (b'(', b')', b'\\'):
                result.extend(esc)
            elif esc[0:1] in b'0123456789':
                # Octal sequence (up to 3 digits)
                octal = esc
                for _ in range(2):
                    i += 1
                    if i >= len(raw):
                        break
                    nx = raw[i:i+1]
                    if nx[0:1] in b'0123456789':
                        octal += nx
                    else:
                        i -= 1
                        break
                result.append(int(octal, 8) & 0xFF)
            else:
                result.extend(esc)
        else:
            result.extend(c)
        i += 1
    try:
        return result.decode('latin-1')
    except Exception:
        return result.decode('utf-8', errors='replace')


def _parse_content_stream(stream_bytes: bytes) -> list[str]:
    """Extract readable text from a PDF content stream (BT...ET blocks).

    Supports both literal strings (text) and hex strings <XXXX>, including
    UTF-16 BE encoded content (with or without \\xFE\\xFF BOM).
    """
    lines: list[str] = []
    # Find BT...ET blocks
    for block in re.findall(rb'BT\s*(.*?)\s*ET', stream_bytes, re.DOTALL):
        # Tj: (text) Tj  — literal
        for m in re.finditer(rb'\(([^)\\]*(?:\\.[^)\\]*)*)\)\s*Tj', block, re.DOTALL):
            decoded = _decode_pdf_string(m.group(1)).strip()
            if decoded:
                lines.append(decoded)
        # Tj: <hex> Tj  — hex-encoded (including UTF-16 BE with BOM)
        for m in re.finditer(rb'<([0-9a-fA-F]+)>\s*Tj', block):
            decoded = _decode_hex_pdf_string(m.group(1)).strip()
            if decoded:
                lines.append(decoded)
        # TJ: [(text1) n (text2) ...] TJ  — literal and/or hex elements
        for array_m in re.finditer(rb'\[(.*?)\]\s*TJ', block, re.DOTALL):
            arr = array_m.group(1)
            # Detect UTF-16 BOM on the first hex element in this array
            first_hex_m = re.search(rb'<([0-9a-fA-F]+)>', arr)
            is_utf16 = False
            if first_hex_m:
                try:
                    fb = bytes.fromhex(first_hex_m.group(1).decode('ascii'))
                    if fb[:2] in (b'\xfe\xff', b'\xff\xfe'):
                        is_utf16 = True
                except Exception:
                    pass
            parts = []
            # Literal strings in array
            for sm in re.finditer(rb'\(([^)\\]*(?:\\.[^)\\]*)*)\)', arr):
                decoded = _decode_pdf_string(sm.group(1))
                if decoded.strip():
                    parts.append(decoded)
            # Hex strings in array — concat without extra spaces (real spaces
            # are encoded as 0x0020 within the hex data)
            hex_chars: list[str] = []
            for sm in re.finditer(rb'<([0-9a-fA-F]+)>', arr):
                ch = _decode_hex_pdf_string(sm.group(1), assume_utf16=is_utf16)
                if ch:
                    hex_chars.append(ch)
            if hex_chars:
                parts.append(''.join(hex_chars))
            if parts:
                lines.append(''.join(parts).strip())
        # ' operator (move to next line + show string)
        for m in re.finditer(rb'\(([^)\\]*(?:\\.[^)\\]*)*)\)\s*\'', block, re.DOTALL):
            decoded = _decode_pdf_string(m.group(1)).strip()
            if decoded:
                lines.append(decoded)
    return lines


def _decompress_stream(raw: bytes) -> bytes:
    """Try zlib inflate (with/without wbits adjustments)."""
    for wbits in (15, -15, 47):
        try:
            return zlib.decompress(raw, wbits)
        except zlib.error:
            pass
    return b''


def _find_streams_fast(raw: bytes) -> list[tuple[bytes, bytes]]:
    """
    Fast byte-scanning approach to locate PDF streams.
    Avoids catastrophic-backtracking regex on large binary files.
    Returns list of (header_bytes, stream_content_bytes).
    """
    results = []
    search_start = 0
    stream_tag  = b'stream'
    end_tag     = b'endstream'

    while True:
        # Find the next 'stream' keyword
        s_pos = raw.find(stream_tag, search_start)
        if s_pos == -1:
            break

        # The character immediately after 'stream' must be \r\n or \n
        after = s_pos + len(stream_tag)
        if raw[after:after+2] == b'\r\n':
            content_start = after + 2
        elif raw[after:after+1] == b'\n':
            content_start = after + 1
        else:
            search_start = s_pos + 1
            continue

        # Find the matching 'endstream'
        e_pos = raw.find(end_tag, content_start)
        if e_pos == -1:
            break

        # Trim trailing \r\n or \n before endstream
        content_end = e_pos
        if raw[content_end-1:content_end] == b'\n':
            content_end -= 1
        if raw[content_end-1:content_end] == b'\r':
            content_end -= 1

        stream_content = raw[content_start:content_end]

        # Look backwards from s_pos for the object header (last '<<' ... '>>')
        # Scan backwards up to 2KB for the dict header
        look_back_start = max(0, s_pos - 2048)
        header_chunk = raw[look_back_start:s_pos]
        # Find the LAST '<<' in the header chunk
        hdr_start = header_chunk.rfind(b'<<')
        if hdr_start != -1:
            obj_header = header_chunk[hdr_start:]
        else:
            obj_header = b''

        results.append((obj_header, stream_content))
        search_start = e_pos + len(end_tag)

    return results


def extract_text_from_pdf(pdf_path: Path) -> tuple[list[str], int, list[str], str]:
    """
    Returns (page_texts, page_count, notes, method_str).
    Tries pdf_extractor_v2 (pypdf) first; falls back to stdlib on failure.
    """
    if _HAS_V2_EXTRACTOR:
        try:
            page_texts, page_count, notes, method_str = _v2_extract(pdf_path)
            if page_texts and page_count > 0:
                total_words = sum(len(t.split()) for t in page_texts)
                if total_words > 0:
                    return page_texts, page_count, notes, method_str
        except Exception as _e:
            pass  # fall through to stdlib

    # ── stdlib fallback ────────────────────────────────────────────────────
    notes: list[str] = []
    try:
        raw = pdf_path.read_bytes()
    except Exception as e:
        return [], 0, [f"File read error: {e}"]

    # ── Step 1: Count pages ─────────────────────────────────────────────────
    # Strategy A: raw byte scan (works for PDF < 1.5 with uncompressed xref)
    page_count = (raw.count(b'/Type /Page')
                  + raw.count(b'/Type/Page')
                  + raw.count(b'/Type\n/Page')
                  + raw.count(b'/Type\r\n/Page'))

    if page_count == 0:
        # Strategy B: PDF 1.5+ with Object Streams (ObjStm) and/or XRef streams.
        # Page dictionaries are compressed inside ObjStm binary blobs.
        # The root /Pages node carries "/Count N" = total leaf page count — the
        # most authoritative value in the file.  We take the MAXIMUM /Count found
        # across all decompressed streams (the root is always the highest; child
        # page-tree nodes carry smaller sub-counts).
        max_count  = 0
        leaf_pages = 0           # count of /Type /Page leafs as a cross-check
        for obj_hdr, sd in _find_streams_fast(raw):
            is_objstm = (b'ObjStm' in obj_hdr or b'/Type /XRef' in obj_hdr
                         or b'/Type/XRef' in obj_hdr)
            for wbits in (-15, 15, 47):
                try:
                    dec = zlib.decompress(sd, wbits)
                    # Check every /Count value found in this decompressed blob
                    for m in re.finditer(rb'/Count\s+(\d+)', dec):
                        v = int(m.group(1))
                        if v > max_count:
                            max_count = v
                    # Also tally leaf /Type /Page entries in ObjStm blobs
                    if is_objstm:
                        leaf_pages += (dec.count(b'/Type /Page')
                                       + dec.count(b'/Type/Page'))
                    break
                except Exception:
                    pass
        # Use max_count (from /Count) when it's reliably above zero; otherwise
        # fall back to the raw leaf-page tally.
        page_count = max_count if max_count > 0 else leaf_pages

    if page_count == 0:
        page_count = 1  # safe default

    # ── Step 2: Extract text from ALL streams (handles Form XObjects) ──────
    all_text_lines: list[str] = []
    streams = _find_streams_fast(raw)

    FONT_MARKERS = (b'glyf', b'vmtx', b'hmtx', b'hhea', b'loca',
                    b'maxp', b'fpgm', b'prep', b'cmap')

    for obj_header, stream_data in streams:
        # Determine filter type
        filter_m = re.search(rb'/Filter\s*\[?/?(\w+)', obj_header)
        filter_type = filter_m.group(1).upper() if filter_m else b''

        if filter_type in (b'FLATEDECODE', b'FL'):
            decompressed = _decompress_stream(stream_data)
            if not decompressed:
                continue
            # Skip OpenType/TrueType font program streams (binary, not text ops)
            if any(marker in decompressed[:512] for marker in FONT_MARKERS):
                continue
            lines = _parse_content_stream(decompressed)
            if lines:
                all_text_lines.extend(lines)
        elif filter_type in (b'', b'ASCIIHEXDECODE', b'ASCIIFILTER'):
            all_text_lines.extend(_parse_content_stream(stream_data))

    # ── Step 3: Fallback — direct BT/ET scan ───────────────────────────────
    if not all_text_lines:
        all_text_lines.extend(_parse_content_stream(raw))
        if all_text_lines:
            notes.append("Used raw-scan fallback for text extraction.")

    if not all_text_lines:
        notes.append("No text extracted — PDF may be image-based or encrypted.")

    # ── Step 4: Distribute text across pages ───────────────────────────────
    if page_count > 0 and all_text_lines:
        chunk = max(1, len(all_text_lines) // page_count)
        page_texts = [
            '\n'.join(all_text_lines[i:i+chunk])
            for i in range(0, len(all_text_lines), chunk)
        ]
        while len(page_texts) < page_count:
            page_texts.append('')
        page_texts = page_texts[:page_count]
    else:
        page_texts = ['\n'.join(all_text_lines)] if all_text_lines else ['']

    return page_texts, page_count, notes, "stdlib_pdf"


# ══════════════════════════════════════════════════════════════════════════════
# PART 3 — Entity Extraction Helpers
# ══════════════════════════════════════════════════════════════════════════════

# Delimiter character class: standard punctuation + space + Unicode dashes
# (\x96 = Latin-1 en-dash, \x97 = em-dash from Windows-1252 via octal escapes)
_DELIM = r'[.:)\u2013\u2014\-\x96\x97\s]*'

ARTICLE_PAT = re.compile(
    r'^(?:ARTICLE|ART\.?|ART[\xcd\xed]CULO)\s*'
    r'(\d+[a-zA-Z]?|[IVXLCDM]+)\b'
    + _DELIM + r'(.*)',
    re.IGNORECASE
)
SECTION_PAT = re.compile(
    r'^(?:SECTION|SEC\.?)\s+'
    r'(\d+[a-zA-Z]?|[IVXLCDM]+)\b'
    + _DELIM + r'(.*)',
    re.IGNORECASE
)
DATE_FULL_PAT = re.compile(
    r'(?:January|February|March|April|May|June|July|August|'
    r'September|October|November|December)'
    r'\s+\d{1,2},?\s+\d{4}',
    re.IGNORECASE
)
DATE_YEAR_PAT = re.compile(r'\b(20\d{2}|19\d{2})\b')
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
_TITLE_NOISE  = re.compile(
    r'(?:\.indd\s+\d+|\.\w{3,4}\s+\d+|^\.{3,}$|^[\W\d]{5,}$)',
    re.IGNORECASE
)
LOCAL_PAT     = re.compile(r'\bLOCAL[\s#]*(\d+)\b', re.IGNORECASE)

REGION_KEYWORDS = [
    "ATLANTIC AREA", "CENTRAL REGION", "SOUTHERN REGION", "WESTERN REGION",
    "NEW ENGLAND", "WESTERN PENNSYLVANIA", "UPSTATE WEST NEW YORK",
    "METRO DETROIT", "METRO PHILADELPHIA", "NORTH DAKOTA", "MINNESOTA",
    "ALASKA", "KENTUCKY", "MICHIGAN", "OHIO", "LOUISVILLE", "NORCAL",
    "PUERTO RICO", "LATIN AMERICA", "CENTRAL PA", "CENTRAL PENNSYLVANIA",
    "NORTH CALIFORNIA", "JOINT COUNCIL 3", "JOINT COUNCIL 28",
    "JOINT COUNCIL 37", "JOINT COUNCIL 38"
]

TOPIC_KEYWORDS: dict[str, list[str]] = {
    "wages":                 ["wage", "rate of pay", "pay rate", "compensation", "hourly rate"],
    "benefits":              ["benefit", "fringe benefit"],
    "health_and_welfare":    ["health and welfare", "health & welfare", "medical", "dental", "vision"],
    "pension":               ["pension", "retirement", "annuity"],
    "hours_of_work":         ["hours of work", "work week", "workday", "shift length"],
    "overtime":              ["overtime", "double time", "premium pay"],
    "seniority":             ["seniority", "bidding", "layoff", "recall"],
    "discipline":            ["discipline", "discharge", "suspension", "termination"],
    "grievance_arbitration": ["grievance", "arbitration", "dispute resolution"],
    "union_security":        ["union security", "checkoff", "dues checkoff", "union membership"],
    "management_rights":     ["management rights", "management prerogative"],
    "leaves_of_absence":     ["leave of absence", "military leave", "family leave", "fmla", "maternity"],
    "safety_and_health":     ["safety", "health and safety", "accident prevention"],
    "holidays":              ["holiday", "observed holiday", "holiday pay"],
    "vacations":             ["vacation"],
    "job_classifications":   ["job classification", "classification", "job grade"],
    "supplemental_work":     ["supplement", "supplemental agreement"],
    "new_technology":        ["new technology", "automation", "technological change"],
    "subcontracting":        ["subcontracting", "outside work", "subcontract"],
    "part_time_employees":   ["part-time", "part time"],
    "full_time_employees":   ["full-time", "full time"],
    "feeders":               ["feeder", "over-the-road", "linehaul"],
    "package_delivery":      ["package driver", "delivery driver", "package car"],
    "air_operations":        ["air driver", "air gateway", "airport"],
    "cartage":               ["cartage"],
    "mechanics":             ["mechanic", "maintenance employee", "vehicle maintenance"],
    "combination_employees": ["combination employee", "combo driver"],
    "general_provisions":    ["general provision", "general terms", "entire agreement"],
}


# ══════════════════════════════════════════════════════════════════════════════
# PART 5 — Corpus Statistical Helpers
# ══════════════════════════════════════════════════════════════════════════════

def field_distribution(values: list) -> dict:
    """Compute distribution statistics for a numeric field. Uses stdlib only."""
    nums = [v for v in values if v is not None and isinstance(v, (int, float))]
    if not nums:
        return {"n": 0, "missing": 39}
    q = _stat.quantiles(nums, n=4) if len(nums) >= 4 else [min(nums), _stat.median(nums), max(nums)]
    return {
        "n":       len(nums),
        "mean":    round(_stat.mean(nums), 2),
        "median":  _stat.median(nums),
        "stdev":   round(_stat.stdev(nums), 2) if len(nums) > 1 else 0.0,
        "min":     min(nums),
        "max":     max(nums),
        "q1":      q[0],
        "q3":      q[2] if len(q) >= 3 else q[-1],
        "iqr":     round((q[2] if len(q) >= 3 else q[-1]) - q[0], 2),
        "missing": 39 - len(nums),
    }


def compute_idf(corpus_tokens: list) -> dict:
    """Compute inverse document frequency for boilerplate detection. Uses stdlib only."""
    N = len(corpus_tokens)
    if N == 0:
        return {}
    df = Counter(term for doc in corpus_tokens for term in set(doc))
    return {term: round(math.log(N / count), 4) for term, count in df.items() if count > 0}


def _doc_id(filename: str) -> str:
    return hashlib.sha256(filename.encode()).hexdigest()[:16]


def _infer_doc_type(fname: str) -> str:
    fu = fname.upper()
    if "MASTER" in fu:      return "National Master Agreement"
    if "SUPPLEMENT" in fu:  return "Supplemental Agreement"
    if "RIDER" in fu:       return "Rider"
    if "ADDENDUM" in fu:    return "Addendum"
    return "Other"


def _infer_local(fname: str, text: str) -> str | None:
    for src in (fname.upper(), text[:3000].upper()):
        m = LOCAL_PAT.search(src)
        if m:
            return m.group(1)
    return None


def _infer_region(fname: str, text: str) -> str | None:
    combined = (fname + " " + text[:4000]).upper()
    for kw in REGION_KEYWORDS:
        if kw in combined:
            return kw.title()
    return None


def _compute_duration(eff: str | None, exp: str | None) -> int | None:
    if not eff or not exp:
        return None
    for fmt in ("%B %d, %Y", "%B %d %Y", "%B %Y"):
        try:
            e = datetime.strptime(eff.strip(), fmt)
            x = datetime.strptime(exp.strip(), fmt)
            return (x - e).days
        except (ValueError, TypeError, AttributeError):
            pass
    return None


def _infer_dates(text: str) -> tuple[str | None, str | None]:
    full_dates = DATE_FULL_PAT.findall(text[:6000])
    if len(full_dates) >= 2:
        def _parse(s: str):
            for fmt in ("%B %d, %Y", "%B %d %Y", "%B %Y"):
                try:
                    return datetime.strptime(s.strip(), fmt)
                except ValueError:
                    pass
            return None
        parsed = [(d, _parse(d)) for d in full_dates if _parse(d)]
        parsed.sort(key=lambda x: x[1])
        if len(parsed) >= 2:
            return parsed[0][0], parsed[-1][0]
        return full_dates[0], full_dates[1] if len(full_dates) > 1 else None
    if full_dates:
        return full_dates[0], None
    years = DATE_YEAR_PAT.findall(text[:4000])
    if years:
        return years[0], years[-1] if len(years) > 1 and years[-1] != years[0] else None
    return None, None


def _infer_title(fname: str, first_page_text: str) -> str:
    # Prefer the first substantive line from the first page
    for line in first_page_text.splitlines():
        clean = line.strip()
        if len(clean) >= 12 and not clean.startswith('%') and not _TITLE_NOISE.search(clean):
            return clean[:200]
    # Fallback: decode filename stem into readable title
    stem = Path(fname).stem
    # Remove leading date prefix like "13124" or "83787-"
    stem = re.sub(r'^[\d\-]+', '', stem)
    # Insert spaces before consecutive uppercase sequences
    stem = re.sub(r'([A-Z][a-z]+)', r' \1', stem).strip()
    return stem[:200] if stem else fname


def _detect_topics(full_text: str) -> list[str]:
    low = full_text.lower()
    found = [
        topic
        for topic, keywords in TOPIC_KEYWORDS.items()
        if sum(low.count(kw) for kw in keywords) >= 2
    ]
    return found if found else ["other"]


def _extract_articles_sections(
    page_texts: list[str]
) -> tuple[list[dict], list[dict], list[dict]]:
    """Returns (articles, sections, headers) parsed from page-split text."""
    articles: list[dict] = []
    sections: list[dict] = []
    headers:  list[dict] = []
    current_article: str | None = None
    # Dedup: articles by article_number, sections by section_id
    seen_art_nums: dict[str, int] = {}   # art_num → index in articles[]
    seen_sec_ids:  set[str]       = set()
    # Reject lines that are clearly in-text references, not article headings
    _REF_START = re.compile(
        r'^[,;]|^(?:of the|of this|shall be|pursuant to|as set forth)',
        re.IGNORECASE
    )

    lines_buffer: list[tuple[str, int]] = [
        (line.strip(), pn)
        for pn, text in enumerate(page_texts, start=1)
        for line in text.splitlines()
        if line.strip()
    ]
    for idx, (stripped, page_num) in enumerate(lines_buffer):
        art_m = ARTICLE_PAT.match(stripped)
        sec_m = SECTION_PAT.match(stripped)

        if art_m:
            art_num   = art_m.group(1).strip()
            raw_title = art_m.group(2).strip()

            # Skip reference lines masquerading as headings
            if _REF_START.match(raw_title):
                wc = len(stripped.split())
                if articles: articles[-1]["word_count"] += wc
                if sections: sections[-1]["word_count"] += wc
                continue

            art_title = raw_title or f"Article {art_num}"

            if art_num in seen_art_nums:
                # Prefer first non-auto title; update if we now have a real one
                art_idx = seen_art_nums[art_num]
                if raw_title and articles[art_idx]["title"] == f"Article {art_num}":
                    articles[art_idx]["title"] = art_title
                    for h in headers:
                        if h["level"] == 2 and h["text"].startswith(f"Article {art_num}:"):
                            h["text"] = f"Article {art_num}: {art_title}"
                            break
                current_article = art_num   # keep context up-to-date
                continue

            current_article = art_num
            seen_art_nums[art_num] = len(articles)
            articles.append({
                "article_number": art_num,
                "title":          art_title,
                "page_start":     page_num,
                "page_end":       None,
                "section_count":  0,
                "word_count":     0
            })
            headers.append({"text": f"Article {art_num}: {art_title}",
                             "page": page_num, "level": 2})

        elif sec_m:
            sec_num   = sec_m.group(1).strip()
            sec_title = sec_m.group(2).strip() or f"Section {sec_num}"
            if sec_title == f"Section {sec_num}":
                for j in range(idx + 1, min(idx + 3, len(lines_buffer))):
                    next_line = lines_buffer[j][0]
                    if next_line and not ARTICLE_PAT.match(next_line) and not SECTION_PAT.match(next_line):
                        sec_title = next_line[:200]
                        break
            sec_id    = f"{current_article or 'NA'}.{sec_num}"

            # Dedup: same section under same article — update title if first was auto-generated
            if sec_id in seen_sec_ids:
                if sec_title != f"Section {sec_num}":
                    for s in reversed(sections):
                        if s["section_id"] == sec_id and s["title"] == f"Section {sec_num}":
                            s["title"] = sec_title
                            break
                continue

            seen_sec_ids.add(sec_id)
            sections.append({
                "section_id":     sec_id,
                "parent_article": current_article,
                "title":          sec_title,
                "page_start":     page_num,
                "page_end":       None,
                "word_count":     0
            })
            headers.append({"text": f"Section {sec_num}: {sec_title}",
                             "page": page_num, "level": 3})
            if articles and articles[-1]["article_number"] == current_article:
                articles[-1]["section_count"] += 1

        else:
            wc = len(stripped.split())
            if articles:
                articles[-1]["word_count"] += wc
            if sections:
                sections[-1]["word_count"] += wc

    # Close page_end spans
    for i, a in enumerate(articles):
        if a["page_end"] is None and i + 1 < len(articles):
            a["page_end"] = articles[i + 1]["page_start"]
    for i, s in enumerate(sections):
        if s["page_end"] is None and i + 1 < len(sections):
            s["page_end"] = sections[i + 1]["page_start"]

    return articles, sections, headers


# ══════════════════════════════════════════════════════════════════════════════
# PART 4 — Main Extraction Driver
# ══════════════════════════════════════════════════════════════════════════════

def extract_pdf_entities(pdf_path: Path) -> dict:
    fname = pdf_path.name
    page_texts, page_count, notes, extraction_method = extract_text_from_pdf(pdf_path)
    full_text      = '\n'.join(page_texts)
    word_count     = len(full_text.split())
    first_page_txt = page_texts[0] if page_texts else ''

    # Semantic date extraction: labeled patterns take priority
    _search_window = full_text[:8000]
    eff_m = EFF_PAT.search(_search_window)
    exp_m = EXP_PAT.search(_search_window)
    if eff_m and exp_m:
        eff_date, exp_date = eff_m.group(1), exp_m.group(1)
    elif eff_m:
        _, exp_date = _infer_dates(full_text)
        eff_date = eff_m.group(1)
    elif exp_m:
        eff_date, _ = _infer_dates(full_text)
        exp_date = exp_m.group(1)
    else:
        eff_date, exp_date = _infer_dates(full_text)
    duration_days = _compute_duration(eff_date, exp_date)
    articles, sections, raw_headers = _extract_articles_sections(page_texts)
    title = _infer_title(fname, first_page_txt)

    # Build headers list: title is always first (level-1)
    headers: list[dict] = [{"text": title, "page": 1, "level": 1}] + raw_headers

    return {
        "document_id":   _doc_id(fname),
        "source_file":   fname,
        "document_type": _infer_doc_type(fname),
        "title":         title,
        "parties": {
            "employer":          "United Parcel Service (UPS)",
            "union":             "International Brotherhood of Teamsters (IBT)",
            "local_number":      _infer_local(fname, full_text),
            "geographic_region": _infer_region(fname, full_text)
        },
        "effective_date":         eff_date,
        "expiration_date":        exp_date,
        "contract_duration_days": duration_days,
        "page_count":             page_count,
        "word_count":      word_count,
        "headers":         headers,
        "sections":        sections,
        "articles":        articles,
        "key_topics":      _detect_topics(full_text),
        "wage_parameters":   extract_wage_parameters(full_text),
        "time_parameters":   extract_time_parameters(full_text),
        "benefit_parameters": extract_benefit_parameters(full_text),
        "cross_references":  extract_cross_references(full_text),
        "extraction_method":  extraction_method,
        "extraction_metadata": {
            "extracted_at":      datetime.now(timezone.utc).isoformat(),
            "extractor_version": EXTRACTOR_VERSION,
            "schema_version":    SCHEMA_VERSION,
            "extraction_notes":  notes
        }
    }


# ══════════════════════════════════════════════════════════════════════════════
# PART 5 — Stdlib JSON Schema Validator (Draft-7 subset)
# ══════════════════════════════════════════════════════════════════════════════

def _validate(instance: object, schema: dict, path: str = "#") -> list[str]:
    """
    Minimal JSON Schema Draft-7 validator (stdlib only).
    Covers: type, required, properties, additionalProperties,
            enum, minLength, maxLength, minimum, uniqueItems, items.
    Returns list of error messages (empty = valid).
    """
    errors: list[str] = []

    def type_check(val, types):
        _type_map = {
            "string":  str,
            "integer": int,
            "number":  (int, float),
            "boolean": bool,
            "array":   list,
            "object":  dict,
            "null":    type(None)
        }
        if isinstance(types, str):
            types = [types]
        for t in types:
            py_t = _type_map.get(t)
            if py_t and isinstance(val, py_t):
                # Distinguish bool from int
                if t == "integer" and isinstance(val, bool):
                    continue
                return True
        return False

    s_type = schema.get("type")
    if s_type and not type_check(instance, s_type):
        errors.append(f"{path}: expected type {s_type!r}, got {type(instance).__name__!r}")
        return errors  # further checks would fail

    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} not in enum {schema['enum']}")

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: length {len(instance)} < minLength {schema['minLength']}")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: length {len(instance)} > maxLength {schema['maxLength']}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: {instance} < minimum {schema['minimum']}")

    if isinstance(instance, dict):
        for req in schema.get("required", []):
            if req not in instance:
                errors.append(f"{path}: missing required field '{req}'")
        props = schema.get("properties", {})
        for k, v in instance.items():
            if k in props:
                errors.extend(_validate(v, props[k], f"{path}/{k}"))
        if schema.get("additionalProperties") is False:
            extra = set(instance.keys()) - set(props.keys())
            if extra:
                errors.append(f"{path}: unexpected additional properties: {extra}")

    if isinstance(instance, list):
        item_schema = schema.get("items", {})
        for i, item in enumerate(instance):
            errors.extend(_validate(item, item_schema, f"{path}[{i}]"))
        if schema.get("uniqueItems"):
            seen = []
            for item in instance:
                if item in seen:
                    errors.append(f"{path}: duplicate item {item!r}")
                else:
                    seen.append(item)

    return errors


def validate_entity(entity: dict) -> list[str]:
    return _validate(entity, CONTRACT_ENTITY_SCHEMA)


# ══════════════════════════════════════════════════════════════════════════════
# PART 6 — Run Pipeline
# ══════════════════════════════════════════════════════════════════════════════

def main():
    # ── Save schema definition ─────────────────────────────────────────────
    schema_path = OUTPUT_DIR / "contract_entity_schema_definition.json"
    with open(schema_path, "w", encoding="utf-8") as f:
        json.dump(CONTRACT_ENTITY_SCHEMA, f, indent=2, ensure_ascii=False)
    print(f"Schema definition saved → {schema_path}\n")

    # ── Extract & save each PDF ────────────────────────────────────────────
    all_entities:  list[dict] = []
    failed_files:  list[tuple[str, str]] = []
    invalid_docs:  list[dict] = []

    for i, pdf_path in enumerate(PDF_FILES, 1):
        out_path = ENTITY_DIR / (pdf_path.stem + "_entities.json")
        if out_path.exists():
            # Re-process if page_count == 1 but word_count suggests multiple pages
            # (symptom of the ObjStm page-count bug in earlier extractor versions)
            try:
                with open(out_path, encoding="utf-8") as f:
                    prev = json.load(f)
                pc = prev.get("page_count", 1)
                wc = prev.get("word_count", 0)
                prev_ver = prev.get("extraction_metadata", {}).get("extractor_version", "")
                # Re-process when: ObjStm page-count=1 bug, sparse text, or
                # extractor version has been bumped (picks up regex/logic fixes).
                needs_reprocess = (
                    (pc == 1 and wc > 500)
                    or (pc >= 10 and wc / max(pc, 1) < 30)
                    or (prev_ver != EXTRACTOR_VERSION)
                )
            except Exception:
                needs_reprocess = False

            if not needs_reprocess:
                try:
                    entity = prev
                    errs = validate_entity(entity)
                    if errs:
                        invalid_docs.append({"source_file": entity["source_file"], "errors": errs})
                    all_entities.append(entity)
                except Exception:
                    pass
                print(f"  [{i:02d}/{len(PDF_FILES)}] {pdf_path.name[:60]:<60} SKIP (already done)")
                continue
            print(f"  [{i:02d}/{len(PDF_FILES)}] {pdf_path.name[:60]:<60} RE-EXTRACT (quality fix) ", end="", flush=True)
        else:
            print(f"  [{i:02d}/{len(PDF_FILES)}] {pdf_path.name[:60]:<60} ", end="", flush=True)
        try:
            entity = extract_pdf_entities(pdf_path)
            errs   = validate_entity(entity)

            if errs:
                invalid_docs.append({"source_file": entity["source_file"], "errors": errs})
                print(f"SCHEMA-WARN ({len(errs)} issues)")
            else:
                print("OK")

            all_entities.append(entity)
            out_path = ENTITY_DIR / (pdf_path.stem + "_entities.json")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(entity, f, indent=2, ensure_ascii=False)

        except Exception as e:
            print(f"ERROR: {e}")
            failed_files.append((pdf_path.name, str(e)))

    # ── Corpus summary ─────────────────────────────────────────────────────
    topic_ctr    = Counter(t for e in all_entities for t in e.get("key_topics", []))
    doctype_ctr  = Counter(e["document_type"] for e in all_entities)
    total_pages  = sum(e.get("page_count", 0) for e in all_entities)
    total_words  = sum(e.get("word_count",  0) for e in all_entities)
    total_arts   = sum(len(e.get("articles",  [])) for e in all_entities)
    total_secs   = sum(len(e.get("sections",  [])) for e in all_entities)

    # ── Statistics block ───────────────────────────────────────────────────
    arts_per_doc = [len(e.get("articles", [])) for e in all_entities]
    secs_per_art = [
        s["word_count"]
        for e in all_entities
        for s in e.get("sections", [])
    ]
    words_per_sec = [
        s["word_count"]
        for e in all_entities
        for s in e.get("sections", [])
        if s.get("word_count", 0) > 0
    ]
    pages_per_doc = [e.get("page_count", 0) for e in all_entities if e.get("page_count", 0) > 0]
    duration_vals = [e.get("contract_duration_days") for e in all_entities]
    wage_vals = [
        w["value"]
        for e in all_entities
        for w in e.get("wage_parameters", [])
        if isinstance(w.get("value"), (int, float))
    ]
    time_vals_days = [
        t["value"]
        for e in all_entities
        for t in e.get("time_parameters", [])
        if isinstance(t.get("value"), (int, float)) and t.get("unit") in ("working_days", "calendar_days", "days")
    ]

    stats_block = {
        "articles_per_document":  field_distribution(arts_per_doc),
        "sections_per_article":   field_distribution(secs_per_art),
        "words_per_section":      field_distribution(words_per_sec),
        "pages_per_document":     field_distribution(pages_per_doc),
        "contract_duration_days": field_distribution(duration_vals),
        "wage_values":            field_distribution(wage_vals),
        "duration_values_days":   field_distribution(time_vals_days),
    }

    # Boilerplate detection via IDF
    corpus_token_lists = [
        list(set((" ".join([
            e.get("title", ""),
            " ".join(s.get("title", "") for s in e.get("sections", []))
        ])).lower().split()))
        for e in all_entities
    ]
    idf_scores = compute_idf(corpus_token_lists)
    boilerplate_terms = {term: score for term, score in idf_scores.items() if score < 0.15}
    stats_block["boilerplate_term_count"] = len(boilerplate_terms)
    stats_block["sample_boilerplate_terms"] = sorted(boilerplate_terms.keys())[:20]

    # Topic co-occurrence matrix
    co_matrix: dict = {}
    for e in all_entities:
        topics = sorted(e.get("key_topics", []))
        for a, b in combinations(topics, 2):
            co_matrix.setdefault(a, {})
            co_matrix[a][b] = co_matrix[a].get(b, 0) + 1
    stats_block["topic_co_occurrence"] = co_matrix

    corpus_summary = {
        "corpus_id":    "ups-labor-contracts-2013-2024",
        "description":  "UPS Labor Contracts entity corpus for synthetic data generation.",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "schema_version":              SCHEMA_VERSION,
        "total_documents":             len(all_entities),
        "failed_extractions":          len(failed_files),
        "schema_invalid_documents":    len(invalid_docs),
        "total_pages":                 total_pages,
        "total_words":                 total_words,
        "total_articles_found":        total_arts,
        "total_sections_found":        total_secs,
        "document_type_distribution":  dict(doctype_ctr),
        "topic_distribution":          dict(topic_ctr.most_common()),
        "documents": [
            {
                "document_id":   e["document_id"],
                "source_file":   e["source_file"],
                "document_type": e["document_type"],
                "title":         e["title"],
                "page_count":    e["page_count"],
                "word_count":    e["word_count"],
                "article_count": len(e["articles"]),
                "section_count": len(e["sections"]),
                "local_number":  e["parties"].get("local_number"),
                "region":        e["parties"].get("geographic_region"),
                "key_topics":    e["key_topics"]
            }
            for e in all_entities
        ],
        "statistics": stats_block,
    }

    summary_path = OUTPUT_DIR / "corpus_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(corpus_summary, f, indent=2, ensure_ascii=False)

    # ── Final report ───────────────────────────────────────────────────────
    print("\n" + "="*60)
    print("  UPS LABOR CONTRACTS — EXTRACTION & VALIDATION REPORT")
    print("="*60)
    print(f"  PDFs processed           : {len(all_entities)}/{len(PDF_FILES)}")
    print(f"  Failed extractions       : {len(failed_files)}")
    print(f"  Schema-valid documents   : {len(all_entities) - len(invalid_docs)}/{len(all_entities)}")
    print(f"  Schema-invalid documents : {len(invalid_docs)}")
    print(f"\n  Total pages              : {total_pages}")
    print(f"  Total words              : {total_words:,}")
    print(f"  Total articles parsed    : {total_arts}")
    print(f"  Total sections parsed    : {total_secs}")
    print(f"\n  Document-type distribution:")
    for k, v in doctype_ctr.most_common():
        print(f"    {k:<35} {v}")
    print(f"\n  Top-10 topics:")
    for k, v in topic_ctr.most_common(10):
        print(f"    {k:<35} {v}")
    print(f"\n  Output directory: {OUTPUT_DIR}")
    print(f"  Entity files     : {len(list(ENTITY_DIR.glob('*_entities.json')))}")
    print("="*60)

    if invalid_docs:
        print("\n  ⚠  Schema issues (first error per doc):")
        for d in invalid_docs:
            print(f"    {d['source_file']}: {d['errors'][0]}")

    if not invalid_docs and not failed_files:
        print("\n  ✓  All extracted documents are schema-valid.")

    return corpus_summary


if __name__ == "__main__":
    main()
