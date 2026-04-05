"""
Table extractor for financial statement pages.

Uses intelligent text-based line parsing to extract structured financial data
from annual report PDFs. The key insight is that financial statement lines
have a text label on the left and numeric values on the right, separated
by spaces. We parse from right to left, peeling off numbers.
"""

import re
from dataclasses import dataclass

import pdfplumber
import pandas as pd

from src.page_classifier import StatementType


@dataclass
class ExtractedTable:
    """Represents an extracted financial table."""

    statement_type: StatementType
    scope: str  # "Standalone" or "Consolidated"
    title: str
    data: pd.DataFrame
    source_pages: list[int]


def _normalize_number(value: str) -> str:
    """Normalize a numeric string: remove commas, convert (x) to -x."""
    cleaned = value.replace(",", "").strip()
    # Handle parenthesized negatives like (123.45)
    match = re.match(r"^\((.+)\)$", cleaned)
    if match:
        cleaned = f"-{match.group(1)}"
    # Handle dash meaning zero/nil
    if cleaned in ("-", "\u2013", "\u2014"):
        return "-"
    return cleaned


def _extract_period_headers(text: str) -> list[str]:
    """Extract period/date column headers from the financial statement text."""
    headers: list[str] = []

    # Try all "March 31, 2022" style dates from header area first
    # This catches dates regardless of prefix
    date_pattern = r"(march\s+\d{1,2},?\s+\d{4})"
    matches = re.findall(date_pattern, text[:1500], re.IGNORECASE)
    if matches:
        # Deduplicate preserving order
        seen: set[str] = set()
        for m in matches:
            normalized = m.strip()
            if normalized.lower() not in seen:
                seen.add(normalized.lower())
                headers.append(normalized)

    return headers


def _is_financial_value(token: str) -> bool:
    """Check if a token looks like a financial value (not a note reference).

    Financial values have decimals (e.g., 2082.49) or are large numbers with
    commas (e.g., 1,234). Single/double digit integers without decimals are
    likely note references, not values.
    """
    # Parenthesized negative: (123.45)
    if re.match(r"^\(\d[\d,]*(?:\.\d+)?\)$", token):
        return True
    # Number with decimal: 123.45, 1,234.56
    if re.match(r"^\d[\d,]*\.\d+$", token):
        return True
    # Large number without decimal but with comma: 1,234
    if re.match(r"^\d{1,3}(,\d{3})+$", token):
        return True
    # Dash/em-dash (nil value)
    if token in ("-", "\u2013", "\u2014"):
        return True
    return False


def _is_note_reference(token: str) -> bool:
    """Check if a token looks like a note reference (e.g., 3, 5A, 40A, 13B, 16A)."""
    if re.match(r"^\d{1,2}[A-Za-z]?$", token):
        num_part = re.match(r"(\d+)", token)
        if num_part and 1 <= int(num_part.group(1)) <= 60:
            return True
    return False


def _parse_financial_line(line: str) -> tuple[str, str, list[str]]:
    """
    Parse a single line from a financial statement.

    Returns (label, note_ref, [numeric_values]).

    Strategy: scan from right to left collecting financial value tokens
    (numbers with decimals, large comma-separated numbers, dashes), then
    check the next token for a note reference, and treat the rest as label.
    """
    line = line.strip()
    if not line:
        return "", "", []

    # Tokenize the line by splitting on whitespace
    tokens = line.split()
    if not tokens:
        return "", "", []

    # From right to left, collect financial value tokens
    values: list[str] = []
    label_tokens: list[str] = list(tokens)

    while label_tokens:
        last = label_tokens[-1]

        if _is_financial_value(last):
            values.insert(0, _normalize_number(last))
            label_tokens.pop()
        else:
            break

    if not values:
        return line, "", []

    # Now check if the last remaining token is a note reference
    note_ref = ""
    if label_tokens and _is_note_reference(label_tokens[-1]):
        note_ref = label_tokens[-1]
        label_tokens.pop()

    label = " ".join(label_tokens).strip()
    return label, note_ref, values


def _is_footer_line(line: str) -> bool:
    """Check if a line is a page footer/signature block."""
    lower = line.lower().strip()
    footer_patterns = [
        "as per our report",
        "chartered accountant",
        "company secretary",
        "membership no",
        "place:",
        "date:",
        "partner",
        "din:",
        "firm registration",
        "icai",
        "the accompanying notes",
        "summary of significant",
        "for deloitte",
        "for and on behalf",
        "for b s r",
        "for s r b c",
        "for price waterhouse",
        "for ernst",
        "whole time director",
        "chief executive",
        "chief financial",
        "executive director",
    ]
    return any(p in lower for p in footer_patterns)


def _is_header_line(line: str) -> bool:
    """Check if a line is a page header (company name, title, amounts disclaimer)."""
    lower = line.lower().strip()
    header_patterns = [
        "financial statements",
        "balance sheet",
        "statement of profit and loss",
        "statement of cash flow",
        "all amounts in",
        "crore rupees",
        "integrated annual report",
        "for the year ended",
        "as at march",
    ]
    # Exact company name header
    if re.match(r"^[a-z\s]+ limited$", lower):
        return True
    # Date-only line like "March 31, 2022 March 31, 2021" (column header remnant)
    if re.match(r"^(march\s+\d{1,2},?\s+\d{4}\s*)+$", lower):
        return True
    return any(p in lower for p in header_patterns)


def _is_column_header_line(line: str) -> bool:
    """Check if line is the column header row (Particulars / Notes / dates)."""
    lower = line.lower().strip()
    return bool(re.match(r"^(particulars|notes?\s)", lower))


def extract_table_from_pages(
    pdf_path: str,
    page_numbers: list[int],
    statement_type: StatementType,
    scope: str,
    verbose: bool = False,
) -> ExtractedTable | None:
    """
    Extract a financial table from the given pages of a PDF.
    """
    all_rows: list[list[str]] = []  # Each row: [label, note, val1, val2, ...]
    period_headers: list[str] = []
    title = ""
    max_values = 0

    with pdfplumber.open(pdf_path) as pdf:
        for page_num in sorted(page_numbers):
            if page_num < 1 or page_num > len(pdf.pages):
                continue

            page = pdf.pages[page_num - 1]
            text = page.extract_text() or ""

            if not text.strip():
                continue

            if not title:
                lines = text.strip().split("\n")
                for line in lines[:5]:
                    lower = line.lower()
                    if any(
                        kw in lower
                        for kw in [
                            "balance sheet",
                            "profit and loss",
                            "cash flow",
                            "statement of",
                        ]
                    ):
                        title = line.strip()
                        break

            if not period_headers:
                period_headers = _extract_period_headers(text)

            # Parse each line
            lines = text.strip().split("\n")
            in_data_section = False
            for line in lines:
                line = line.strip()
                if not line:
                    continue

                # Detect start of data section
                if _is_column_header_line(line):
                    in_data_section = True
                    continue

                # Skip headers before data starts
                if not in_data_section and _is_header_line(line):
                    continue

                # Once we see a data-like line, mark in_data_section
                if not in_data_section:
                    # Check if this line has data characteristics
                    _, _, vals = _parse_financial_line(line)
                    if vals:
                        in_data_section = True
                    elif line.isupper() and len(line) < 40:
                        # Section header like "ASSETS"
                        in_data_section = True
                    else:
                        continue

                # Skip footers
                if _is_footer_line(line):
                    continue
                # Skip decorative lines
                if all(c in "-=_| " for c in line):
                    continue
                # Skip page numbers
                if re.match(r"^\d{1,3}\s*$", line):
                    continue
                # Skip header lines that slipped through
                if _is_header_line(line):
                    continue

                label, note_ref, values = _parse_financial_line(line)

                if not label and not values:
                    continue

                row = [label, note_ref] + values
                max_values = max(max_values, len(values))
                all_rows.append(row)

    if not all_rows:
        return None

    # Build column names
    columns = ["Particulars", "Note"]
    if period_headers:
        for i in range(max_values):
            if i < len(period_headers):
                columns.append(period_headers[i])
            else:
                columns.append(f"Value_{i + 1}")
    else:
        for i in range(max_values):
            columns.append(f"Value_{i + 1}")

    # Pad rows to same length
    expected_len = 2 + max_values
    padded_rows: list[list[str]] = []
    for row in all_rows:
        padded = row + [""] * (expected_len - len(row))
        padded_rows.append(padded[:expected_len])

    df = pd.DataFrame(padded_rows, columns=columns[:expected_len])

    # Clean up: remove rows that are entirely empty
    df = df[df.apply(lambda r: any(str(v).strip() for v in r), axis=1)]

    # Remove Note column if entirely empty
    if df["Note"].str.strip().eq("").all():
        df = df.drop(columns=["Note"])

    df = df.reset_index(drop=True)

    return ExtractedTable(
        statement_type=statement_type,
        scope=scope,
        title=title or statement_type.value.replace("_", " "),
        data=df,
        source_pages=sorted(page_numbers),
    )
