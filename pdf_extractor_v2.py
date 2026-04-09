"""
pdf_extractor_v2.py — Three-Tier PDF Extraction Layer
======================================================
Replaces the pure-stdlib extract_text_from_pdf in extract_contracts.py with a
layered extraction strategy that resolves TJ kerning artifacts and improves
words-per-page yield.

Tiers
-----
1. pdfminer.six layout analysis with tuned LAParams — primary path for all PDFs.
   Yields PageLine objects with font_size and is_bold metadata.
2. pdfminer.six simple extract_text per page — fallback when Tier 1 yields
   < 50 chars on > 50% of pages.
3. PyMuPDF (fitz) — conditional fallback for known CID-font documents that
   cannot be decoded by pdfminer (no /ToUnicode CMap).

CID-font documents (Tier 3 targets):
    13124UPSLOCAL769LATINAMERICAINCRIDER.pdf
    100424UPSLOCAL901PUERTORICOSUPPLEMENTALAGREEMENT.pdf

Usage
-----
    from pdf_extractor_v2 import extract_pdf, extract_headings

    result = extract_pdf("path/to/file.pdf")
    print(result.tier_used, len(result.page_lines))
    headings = extract_headings(result.page_lines)

Dependencies
------------
    pdfminer.six>=20221105   (required for Tier 1 and Tier 2)
    pdfplumber>=0.11.0       (available for future table extraction in Phase 4)
    PyMuPDF>=1.24.0          (optional; AGPL-3.0; needed only for 2 CID documents)

Install via:
    pip install pdfminer.six pdfplumber
"""

from __future__ import annotations

import io
import logging
from collections import namedtuple
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)

# ── Public namedtuples ─────────────────────────────────────────────────────────

PageLine = namedtuple("PageLine", ["text", "page", "font_size", "is_bold", "x0", "y0"])
"""A single line of text from a PDF page with positional and font metadata."""

ExtractionResult = namedtuple(
    "ExtractionResult",
    ["page_lines", "tier_used", "words_per_page_list", "extraction_method_str"],
)
"""
Result returned by extract_pdf().

Fields
------
page_lines : list[PageLine]
tier_used  : int (1, 2, or 3)
words_per_page_list : list[int] — word count per page (index 0 = page 1)
extraction_method_str : str — human-readable description, e.g. "pdfminer-tier1"
"""

# ── Known CID-font documents (Tier 3 targets) ─────────────────────────────────

_CID_FONT_DOCUMENTS = frozenset(
    {
        "13124UPSLOCAL769LATINAMERICAINCRIDER.pdf",
        "100424UPSLOCAL901PUERTORICOSUPPLEMENTALAGREEMENT.pdf",
    }
)

# ── pypdf availability check ────────────────────────────────────────────────
try:
    import pypdf as _pypdf
    _HAS_PYPDF = True
except ImportError:
    _pypdf = None  # type: ignore[assignment]
    _HAS_PYPDF = False

# ── LAParams for Tier 1 ────────────────────────────────────────────────────────
# boxes_flow=None: disables multi-column text reordering (critical for single-
#   column contract PDFs — the default 0.5 interleaves adjacent columns).
# char_margin=2.5: slightly wider than default 2.0; reduces spurious spaces
#   between kerned characters in InDesign-exported PDFs.
# line_overlap=0.5: standard; prevents duplicate lines.
# line_margin=0.5: controls when adjacent lines are merged into one text box.
# word_margin=0.1: tight; respects original word boundaries.

try:
    from pdfminer.layout import LAParams

    BODY_LAPARAMS = LAParams(
        line_overlap=0.5,
        char_margin=2.5,
        line_margin=0.5,
        word_margin=0.1,
        boxes_flow=None,
        all_texts=False,
    )
except ImportError:
    BODY_LAPARAMS = None  # type: ignore[assignment]


# ══════════════════════════════════════════════════════════════════════════════
# Tier 0 — pypdf (primary; pure Python; installed from GitHub)
# ══════════════════════════════════════════════════════════════════════════════

def _tier0_extract(pdf_path: Path) -> List[PageLine]:
    """
    Extract text with pypdf — primary extraction path.
    Returns a PageLine per text line in reading order, with font_size=0
    (pypdf does not expose font-size reliably).
    """
    if not _HAS_PYPDF:
        return []
    try:
        reader = _pypdf.PdfReader(str(pdf_path))
        page_lines: List[PageLine] = []
        for page_num, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            for raw_line in text.splitlines():
                stripped = raw_line.strip()
                if not stripped:
                    continue
                page_lines.append(PageLine(
                    text=stripped,
                    page=page_num,
                    font_size=0.0,
                    is_bold=False,
                    x0=0.0,
                    y0=0.0,
                ))
        return page_lines
    except Exception as exc:  # noqa: BLE001
        logger.warning("Tier 0 (pypdf) failed for %s: %s", pdf_path.name, exc)
        return []


# ══════════════════════════════════════════════════════════════════════════════
# Tier 1 — pdfminer.six layout analysis
# ══════════════════════════════════════════════════════════════════════════════

def _tier1_extract(pdf_path: Path) -> List[PageLine]:
    """
    Extract text with full layout analysis using pdfminer.six.

    Iterates LTPage → LTTextBox → LTTextLine → LTChar/LTAnon to produce
    PageLine objects with font_size and is_bold set from the first LTChar
    in each line.

    Returns
    -------
    list[PageLine]  — empty list if pdfminer.six is not installed or extraction
                      raises an unrecoverable error.
    """
    try:
        from pdfminer.high_level import extract_pages
        from pdfminer.layout import LTTextBox, LTTextLine, LTChar
    except ImportError:
        logger.warning(
            "pdfminer.six is not installed. "
            "Install with: pip install pdfminer.six"
        )
        return []

    page_lines: List[PageLine] = []

    try:
        for page_num, page_layout in enumerate(
            extract_pages(str(pdf_path), laparams=BODY_LAPARAMS), start=1
        ):
            for element in page_layout:
                if not isinstance(element, LTTextBox):
                    continue
                for line in element:
                    if not isinstance(line, LTTextLine):
                        continue
                    line_text = line.get_text().rstrip("\n")
                    if not line_text.strip():
                        continue
                    # Font metrics from first LTChar in the line
                    font_size = 0.0
                    is_bold = False
                    for char in line:
                        if isinstance(char, LTChar):
                            font_size = char.size
                            is_bold = "bold" in char.fontname.lower()
                            break
                    page_lines.append(
                        PageLine(
                            text=line_text,
                            page=page_num,
                            font_size=round(font_size, 1),
                            is_bold=is_bold,
                            x0=round(line.x0, 1),
                            y0=round(line.y0, 1),
                        )
                    )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Tier 1 extraction failed for %s: %s", pdf_path.name, exc)
        return []

    return page_lines


# ══════════════════════════════════════════════════════════════════════════════
# Tier 2 — pdfminer.six simple extract_text per page
# ══════════════════════════════════════════════════════════════════════════════

def _tier2_extract(pdf_path: Path) -> List[PageLine]:
    """
    Fallback extraction using pdfminer.six extract_text per page.

    Produces PageLine objects with font_size=0.0 and is_bold=False (no layout
    metadata available from simple extraction).

    Returns
    -------
    list[PageLine]  — empty list if pdfminer.six is not installed.
    """
    try:
        from pdfminer.high_level import extract_text_to_fp
        from pdfminer.layout import LAParams as _LAParams
    except ImportError:
        logger.warning(
            "pdfminer.six is not installed. "
            "Install with: pip install pdfminer.six"
        )
        return []

    # Simple LAParams: accept default column handling since Tier 2 is a fallback
    simple_laparams = _LAParams()
    page_lines: List[PageLine] = []

    try:
        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()

        # extract_text per page by using page numbers
        from pdfminer.high_level import extract_pages as _extract_pages
        from pdfminer.layout import LTPage

        for page_num, page_layout in enumerate(
            _extract_pages(str(pdf_path), laparams=simple_laparams), start=1
        ):
            buf = io.StringIO()
            # Use extract_text_to_fp on just this page range
            extract_text_to_fp(
                io.BytesIO(pdf_bytes),
                buf,
                laparams=simple_laparams,
                page_numbers=[page_num - 1],  # 0-based
                output_type="text",
                codec="utf-8",
            )
            page_text = buf.getvalue()
            for raw_line in page_text.splitlines():
                if not raw_line.strip():
                    continue
                page_lines.append(
                    PageLine(
                        text=raw_line,
                        page=page_num,
                        font_size=0.0,
                        is_bold=False,
                        x0=0.0,
                        y0=0.0,
                    )
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Tier 2 extraction failed for %s: %s", pdf_path.name, exc)
        return []

    return page_lines


# ══════════════════════════════════════════════════════════════════════════════
# Tier 3 — PyMuPDF (fitz) for CID-font documents
# ══════════════════════════════════════════════════════════════════════════════

def _tier3_extract(pdf_path: Path) -> List[PageLine]:
    """
    Fallback extraction using PyMuPDF (fitz) for CID-font documents.

    PyMuPDF uses MuPDF's native font engine which handles CID fonts without a
    /ToUnicode CMap via embedded glyph tables, producing correct Unicode text
    where pdfminer.six would output (cid:XX) literals.

    PyMuPDF is imported conditionally; if not installed a warning is logged and
    an empty list is returned (caller falls back to Tier 2).

    Returns
    -------
    list[PageLine]  — empty list if PyMuPDF is not installed.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.warning(
            "PyMuPDF (fitz) is not installed. "
            "Install with: pip install PyMuPDF  "
            "(Note: PyMuPDF is AGPL-3.0 licensed.)"
        )
        return []

    page_lines: List[PageLine] = []

    try:
        doc = fitz.open(str(pdf_path))
        for page_num, page in enumerate(doc, start=1):
            blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)[
                "blocks"
            ]
            for block in blocks:
                if block.get("type") != 0:  # 0 = text
                    continue
                for line in block.get("lines", []):
                    line_parts: List[str] = []
                    font_size = 0.0
                    is_bold = False
                    x0 = 0.0
                    y0 = 0.0
                    first_span = True
                    for span in line.get("spans", []):
                        span_text = span.get("text", "")
                        if not span_text.strip():
                            continue
                        line_parts.append(span_text)
                        if first_span:
                            font_size = span.get("size", 0.0)
                            flags = span.get("flags", 0)
                            is_bold = bool(flags & 16)  # bit 4 = bold
                            bbox = span.get("bbox", (0, 0, 0, 0))
                            x0 = round(bbox[0], 1)
                            y0 = round(bbox[1], 1)
                            first_span = False
                    combined = "".join(line_parts).strip()
                    if not combined:
                        continue
                    page_lines.append(
                        PageLine(
                            text=combined,
                            page=page_num,
                            font_size=round(font_size, 1),
                            is_bold=is_bold,
                            x0=x0,
                            y0=y0,
                        )
                    )
        doc.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Tier 3 (PyMuPDF) extraction failed for %s: %s", pdf_path.name, exc)
        return []

    return page_lines


# ══════════════════════════════════════════════════════════════════════════════
# Quality helpers
# ══════════════════════════════════════════════════════════════════════════════

def _compute_words_per_page(page_lines: List[PageLine]) -> List[int]:
    """Return list of word counts indexed by page (index 0 = page 1)."""
    if not page_lines:
        return []
    max_page = max(pl.page for pl in page_lines)
    counts: List[int] = [0] * max_page
    for pl in page_lines:
        counts[pl.page - 1] += len(pl.text.split())
    return counts


def _avg_words_per_page(words_per_page: List[int]) -> float:
    """Average words per page; 0.0 for empty input."""
    if not words_per_page:
        return 0.0
    return sum(words_per_page) / len(words_per_page)


def _tier1_quality_ok(page_lines: List[PageLine], page_count: int) -> bool:
    """
    Return True when Tier 1 output is considered usable.

    Criterion: average words per page >= 50, OR the document has <= 5 pages
    (short documents legitimately have low word counts).
    """
    if page_count <= 5:
        return bool(page_lines)
    if not page_lines:
        return False
    wpp = _avg_words_per_page(_compute_words_per_page(page_lines))
    return wpp >= 50.0


def _count_pages_with_few_chars(page_lines: List[PageLine], threshold: int = 50) -> tuple[int, int]:
    """
    Return (pages_below_threshold, total_pages).
    A page is counted as 'few chars' when its total char count < threshold.
    """
    if not page_lines:
        return 0, 0
    page_chars: dict[int, int] = {}
    for pl in page_lines:
        page_chars[pl.page] = page_chars.get(pl.page, 0) + len(pl.text)
    total = len(page_chars)
    below = sum(1 for c in page_chars.values() if c < threshold)
    return below, total


# ══════════════════════════════════════════════════════════════════════════════
# Main extraction entry point
# ══════════════════════════════════════════════════════════════════════════════

def extract_pdf(pdf_path: str | Path) -> ExtractionResult:
    """
    Extract text from a PDF using a three-tier fallback strategy.

    Parameters
    ----------
    pdf_path : str or Path
        Path to the PDF file.

    Returns
    -------
    ExtractionResult
        Named tuple with page_lines, tier_used (1/2/3),
        words_per_page_list, and extraction_method_str.

    Tier selection logic
    --------------------
    1. If the filename is in _CID_FONT_DOCUMENTS → try Tier 3 first (PyMuPDF).
       If PyMuPDF is not installed, fall through to Tier 1.
    2. Try Tier 1 (pdfminer.six layout analysis).
       If Tier 1 yields < 50 chars on > 50% of pages → try Tier 2.
    3. If Tier 1 avg words/page < 50 and total pages > 5 → try Tier 2.
    4. If Tier 2 also fails → fall back to Tier 3 with a warning.
    """
    pdf_path = Path(pdf_path)
    filename = pdf_path.name
    is_cid_doc = filename in _CID_FONT_DOCUMENTS

    # ── Tier 0: pypdf (primary path) ─────────────────────────────────────────
    if _HAS_PYPDF:
        tier0_lines = _tier0_extract(pdf_path)
        if tier0_lines:
            wpp = _compute_words_per_page(tier0_lines)
            avg = _avg_words_per_page(wpp)
            # Accept pypdf result when avg w/p is reasonable (>= 30) or short doc
            page_count_est = max(pl.page for pl in tier0_lines) if tier0_lines else 1
            if avg >= 30 or page_count_est <= 5:
                logger.info(
                    "Tier 0 (pypdf) succeeded for %s: %d lines, avg %.1f w/p",
                    filename, len(tier0_lines), avg,
                )
                return ExtractionResult(
                    page_lines=tier0_lines,
                    tier_used=0,
                    words_per_page_list=wpp,
                    extraction_method_str="pypdf-tier0",
                )

    # ── CID-font fast path (PyMuPDF) ─────────────────────────────────────────
    if is_cid_doc:
        logger.info("CID-font document detected: %s — trying Tier 3 first.", filename)
        tier3_lines = _tier3_extract(pdf_path)
        if tier3_lines:
            wpp = _compute_words_per_page(tier3_lines)
            logger.info(
                "Tier 3 succeeded for %s: %d lines, avg %.1f w/p",
                filename,
                len(tier3_lines),
                _avg_words_per_page(wpp),
            )
            return ExtractionResult(
                page_lines=tier3_lines,
                tier_used=3,
                words_per_page_list=wpp,
                extraction_method_str="pymupdf-tier3-cid",
            )
        logger.warning(
            "Tier 3 unavailable for CID document %s — falling through to Tier 1.",
            filename,
        )

    # ── Tier 1: pdfminer layout analysis ──────────────────────────────────────
    tier1_lines = _tier1_extract(pdf_path)

    if tier1_lines:
        # Quality gate: if > 50% of pages have < 50 chars, fall back to Tier 2
        below, total = _count_pages_with_few_chars(tier1_lines, threshold=50)
        frac_sparse = (below / total) if total > 0 else 0.0
        avg_wpp = _avg_words_per_page(_compute_words_per_page(tier1_lines))

        if total > 5 and (frac_sparse > 0.5 or avg_wpp < 50.0):
            logger.info(
                "Tier 1 quality check failed for %s "
                "(sparse_frac=%.2f, avg_wpp=%.1f) — falling back to Tier 2.",
                filename,
                frac_sparse,
                avg_wpp,
            )
        else:
            wpp = _compute_words_per_page(tier1_lines)
            logger.info(
                "Tier 1 succeeded for %s: %d lines, avg %.1f w/p",
                filename,
                len(tier1_lines),
                _avg_words_per_page(wpp),
            )
            return ExtractionResult(
                page_lines=tier1_lines,
                tier_used=1,
                words_per_page_list=wpp,
                extraction_method_str="pdfminer-tier1",
            )

    # ── Tier 2: pdfminer simple extraction ───────────────────────────────────
    logger.info("Attempting Tier 2 extraction for %s.", filename)
    tier2_lines = _tier2_extract(pdf_path)

    if tier2_lines:
        wpp = _compute_words_per_page(tier2_lines)
        logger.info(
            "Tier 2 succeeded for %s: %d lines, avg %.1f w/p",
            filename,
            len(tier2_lines),
            _avg_words_per_page(wpp),
        )
        return ExtractionResult(
            page_lines=tier2_lines,
            tier_used=2,
            words_per_page_list=wpp,
            extraction_method_str="pdfminer-tier2",
        )

    # ── Tier 3 last resort ───────────────────────────────────────────────────
    logger.warning(
        "Tier 1 and Tier 2 both failed for %s — attempting Tier 3 (PyMuPDF).",
        filename,
    )
    tier3_lines = _tier3_extract(pdf_path)
    wpp = _compute_words_per_page(tier3_lines)
    return ExtractionResult(
        page_lines=tier3_lines,
        tier_used=3,
        words_per_page_list=wpp,
        extraction_method_str="pymupdf-tier3-fallback",
    )


# ══════════════════════════════════════════════════════════════════════════════
# Heading extraction stub — implemented in Phase 2
# ══════════════════════════════════════════════════════════════════════════════

def extract_headings(page_lines: List[PageLine]) -> List[PageLine]:
    """
    Detect heading lines from a list of PageLine objects.

    This is a stub that returns an empty list; font-metric heading detection
    is implemented in Phase 2 (pdf_heading_detector.py).

    Parameters
    ----------
    page_lines : list[PageLine]
        Output from extract_pdf().page_lines.

    Returns
    -------
    list[PageLine]  — Heading lines (currently always empty).
    """
    return []


# ══════════════════════════════════════════════════════════════════════════════
# Convenience function: page-accurate text extraction for extract_contracts.py
# ══════════════════════════════════════════════════════════════════════════════

def extract_text_pages(pdf_path: str | Path) -> tuple[list[str], int, list[str], str]:
    """
    High-level interface for extract_contracts.py.

    Returns
    -------
    (page_texts, page_count, notes, method_str)
        page_texts  : list[str] — one string per page, in reading order
        page_count  : int
        notes       : list[str] — warnings / diagnostic messages
        method_str  : str — e.g. "pypdf-tier0"
    """
    result = extract_pdf(pdf_path)
    if not result.page_lines:
        return [], 0, ["pdf_extractor_v2: no text extracted"], result.extraction_method_str

    # Group lines by page number
    page_num_max = max(pl.page for pl in result.page_lines)
    page_texts: list[str] = []
    for pn in range(1, page_num_max + 1):
        lines_for_page = [pl.text for pl in result.page_lines if pl.page == pn]
        page_texts.append("\n".join(lines_for_page))

    notes: list[str] = []
    avg_wpp = _avg_words_per_page(result.words_per_page_list)
    if avg_wpp < 30 and page_num_max > 5:
        notes.append(f"Low extraction yield: {avg_wpp:.0f} words/page (method={result.extraction_method_str})")

    return page_texts, page_num_max, notes, result.extraction_method_str


# ══════════════════════════════════════════════════════════════════════════════
# CLI smoke test
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python pdf_extractor_v2.py <path_to_pdf>")
        sys.exit(1)

    logging.basicConfig(level=logging.INFO)
    path = Path(sys.argv[1])
    result = extract_pdf(path)
    pages = len(set(pl.page for pl in result.page_lines)) if result.page_lines else 0
    wpp = _avg_words_per_page(result.words_per_page_list)
    print(f"File     : {path.name}")
    print(f"Tier     : {result.tier_used}  ({result.extraction_method_str})")
    print(f"Lines    : {len(result.page_lines)}")
    print(f"Pages    : {pages}")
    print(f"Avg w/p  : {wpp:.1f}")
    if result.page_lines:
        print(f"Sample   : {result.page_lines[0].text!r}")
