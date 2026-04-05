"""
Page classifier for annual report PDFs.

Identifies which pages contain Balance Sheet, P&L, Cash Flow statements,
and their associated footnotes. Distinguishes standalone vs consolidated sections.
"""

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import pdfplumber


class StatementType(Enum):
    BALANCE_SHEET = "Balance_Sheet"
    PROFIT_AND_LOSS = "Profit_and_Loss"
    CASH_FLOW = "Cash_Flow"
    CHANGES_IN_EQUITY = "Changes_in_Equity"
    FOOTNOTES = "Footnotes"
    OTHER = "Other"


class SectionScope(Enum):
    STANDALONE = "Standalone"
    CONSOLIDATED = "Consolidated"
    UNKNOWN = "Unknown"


@dataclass
class PageInfo:
    page_number: int  # 1-indexed
    statement_type: StatementType
    scope: SectionScope
    title: str = ""
    note_numbers: list[int] = field(default_factory=list)


# Patterns to identify statement types
BALANCE_SHEET_PATTERNS = [
    r"balance\s+sheet",
    r"statement\s+of\s+financial\s+position",
]

PNL_PATTERNS = [
    r"statement\s+of\s+profit\s+and\s+loss",
    r"profit\s+and\s+loss\s+statement",
    r"profit\s+and\s+loss\s+account",
    r"statement\s+of\s+income",
]

CASH_FLOW_PATTERNS = [
    r"statement\s+of\s+cash\s+flow",
    r"cash\s+flow\s+statement",
]

CHANGES_IN_EQUITY_PATTERNS = [
    r"statement\s+of\s+changes\s+in\s+equity",
    r"changes\s+in\s+equity",
]

FOOTNOTES_PATTERNS = [
    r"notes\s+to\s+(?:the\s+)?(?:standalone\s+|consolidated\s+)?financial\s+statements",
    r"notes\s+to\s+(?:the\s+)?(?:standalone\s+|consolidated\s+)?accounts",
    r"notes\s+forming\s+part\s+of",
]

STANDALONE_PATTERNS = [
    r"\bstandalone\b",
    r"\bstand[\-\s]alone\b",
]

CONSOLIDATED_PATTERNS = [
    r"\bconsolidated\b",
]


def _match_any(text: str, patterns: list[str]) -> bool:
    """Check if text matches any of the given regex patterns (case-insensitive)."""
    for pattern in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def _detect_scope(text: str, current_scope: SectionScope) -> SectionScope:
    """Detect whether text refers to standalone or consolidated statements."""
    text_lower = text.lower()
    if _match_any(text_lower, CONSOLIDATED_PATTERNS):
        return SectionScope.CONSOLIDATED
    if _match_any(text_lower, STANDALONE_PATTERNS):
        return SectionScope.STANDALONE
    return current_scope


def _extract_note_numbers(text: str) -> list[int]:
    """Extract note reference numbers from a financial statement page."""
    note_nums = set()
    # Match patterns like "Note 3", "Notes 3", or just standalone numbers
    # in the notes column of financial statements
    for match in re.finditer(
        r"(?:note[s]?\s+)?(\d{1,3})(?:\s|$|\n)", text, re.IGNORECASE
    ):
        num = int(match.group(1))
        if 1 <= num <= 60:  # Reasonable note number range
            note_nums.add(num)
    return sorted(note_nums)


def _is_non_financial_section(text: str) -> bool:
    """Check if a page is from a non-financial section (MD&A, governance, etc.)."""
    lower = text[:600].lower()
    non_financial_markers = [
        "management discussion",
        "management's discussion",
        "corporate governance",
        "board's report",
        "director's report",
        "corporate overview",
        "business responsibility",
        "key performance indicators",
        "year in review",
        "shareholder information",
        "auditor's report",
        "independent auditor",
    ]
    return any(marker in lower for marker in non_financial_markers)


def _is_financial_statements_section(text: str) -> bool:
    """Check if a page is likely within the actual Financial Statements section.

    Pages in the corporate overview, MD&A, or governance sections may mention
    "balance sheet" or "cash flow" in passing. We filter those out by requiring
    structural markers typical of actual financial statement pages:
    - "Particulars" header row
    - "(All amounts in" denomination line
    - Note references pattern
    - Signature blocks
    """
    # First, reject pages from known non-financial sections
    if _is_non_financial_section(text):
        return False

    lower = text.lower()
    financial_markers = [
        "particulars",
        "(all amounts in",
        "all amounts in crore",
        "all amounts in lakhs",
        "as per our report of even date",
        "notes to financial statements",
        "notes to the financial",
        "significant accounting policies",
    ]
    return any(marker in lower for marker in financial_markers)


def _is_actual_statement_page(
    text: str, header_text: str, statement_type: StatementType
) -> bool:
    """Verify that a page is an actual financial statement, not a mention in overview."""
    # For footnotes, the header match is sufficient
    if statement_type == StatementType.FOOTNOTES:
        return _is_financial_statements_section(text)

    # For main statements, require the title to appear in a title-like position
    # (first ~5 lines) rather than buried in paragraph text
    lines = text.strip().split("\n")
    title_area = "\n".join(lines[:6]).lower()

    type_keywords: dict[StatementType, list[str]] = {
        StatementType.BALANCE_SHEET: ["balance sheet"],
        StatementType.PROFIT_AND_LOSS: ["profit and loss", "statement of income"],
        StatementType.CASH_FLOW: ["cash flow"],
        StatementType.CHANGES_IN_EQUITY: ["changes in equity"],
    }

    keywords = type_keywords.get(statement_type, [])
    title_match = any(kw in title_area for kw in keywords)

    if not title_match:
        return False

    # Must also have financial statement structural markers
    return _is_financial_statements_section(text)


def classify_pages(pdf_path: str, verbose: bool = False) -> list[PageInfo]:
    """
    Classify all pages of a PDF into statement types and scopes.

    Returns a list of PageInfo objects for pages that contain financial statements.
    """
    pages: list[PageInfo] = []
    current_scope = SectionScope.STANDALONE  # Default to standalone first

    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            page_num = i + 1
            text = page.extract_text() or ""
            if not text.strip():
                continue

            # Use first 800 chars for classification (header area)
            header_text = text[:800]

            # Detect scope transitions
            current_scope = _detect_scope(header_text, current_scope)

            # Classify the page
            statement_type = StatementType.OTHER
            title = ""

            if _match_any(header_text, BALANCE_SHEET_PATTERNS):
                statement_type = StatementType.BALANCE_SHEET
                title = "Balance Sheet"
            elif _match_any(header_text, PNL_PATTERNS):
                statement_type = StatementType.PROFIT_AND_LOSS
                title = "Statement of Profit and Loss"
            elif _match_any(header_text, CASH_FLOW_PATTERNS):
                statement_type = StatementType.CASH_FLOW
                title = "Statement of Cash Flows"
            elif _match_any(header_text, CHANGES_IN_EQUITY_PATTERNS):
                statement_type = StatementType.CHANGES_IN_EQUITY
                title = "Statement of Changes in Equity"
            elif _match_any(header_text, FOOTNOTES_PATTERNS):
                statement_type = StatementType.FOOTNOTES
                title = "Notes to Financial Statements"

            if statement_type != StatementType.OTHER:
                # Verify this is an actual financial statement page,
                # not a passing mention in corporate overview/MD&A
                if not _is_actual_statement_page(text, header_text, statement_type):
                    if verbose:
                        print(f"  Page {page_num}: SKIPPED (not actual statement page)")
                    continue

                note_numbers = (
                    _extract_note_numbers(text)
                    if statement_type != StatementType.FOOTNOTES
                    else []
                )
                page_info = PageInfo(
                    page_number=page_num,
                    statement_type=statement_type,
                    scope=current_scope,
                    title=title,
                    note_numbers=note_numbers,
                )
                pages.append(page_info)

                if verbose:
                    print(
                        f"  Page {page_num}: {current_scope.value} {statement_type.value} - {title}"
                    )

    return pages


def get_statement_page_ranges(
    pages: list[PageInfo],
) -> dict[tuple[SectionScope, StatementType], list[int]]:
    """
    Group classified pages into ranges by (scope, statement_type).

    Returns a dict mapping (scope, type) -> list of page numbers.
    """
    ranges: dict[tuple[SectionScope, StatementType], list[int]] = {}

    for page in pages:
        key = (page.scope, page.statement_type)
        if key not in ranges:
            ranges[key] = []
        ranges[key].append(page.page_number)

    return ranges


def get_footnote_page_range(
    pages: list[PageInfo], scope: SectionScope
) -> tuple[Optional[int], Optional[int]]:
    """
    Get the start and end page numbers for footnotes of a given scope.

    Returns (start_page, end_page) or (None, None) if not found.
    """
    footnote_pages = [
        p.page_number
        for p in pages
        if p.statement_type == StatementType.FOOTNOTES and p.scope == scope
    ]

    if not footnote_pages:
        return None, None

    return min(footnote_pages), max(footnote_pages)
