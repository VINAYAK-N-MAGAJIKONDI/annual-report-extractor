"""
Footnote extractor for annual report PDFs.

Extracts the notes to financial statements that are referenced
by the Balance Sheet, P&L, and Cash Flow statements.
"""

import re

import pdfplumber
import pandas as pd

from src.page_classifier import SectionScope, PageInfo, StatementType


def extract_footnotes(
    pdf_path: str,
    pages: list[PageInfo],
    scope: SectionScope,
    verbose: bool = False,
) -> pd.DataFrame:
    """
    Extract footnotes from the notes section of financial statements.

    Groups notes by their note number and captures the note title,
    content, and any sub-tables within the note.
    """
    # Find footnote pages for this scope
    footnote_pages = [
        p.page_number
        for p in pages
        if p.statement_type == StatementType.FOOTNOTES and p.scope == scope
    ]

    if not footnote_pages:
        if verbose:
            print(f"  No footnote pages found for {scope.value}")
        return pd.DataFrame()

    footnote_pages.sort()

    # Collect referenced note numbers from financial statement pages
    referenced_notes: set[int] = set()
    for p in pages:
        if p.scope == scope and p.statement_type in (
            StatementType.BALANCE_SHEET,
            StatementType.PROFIT_AND_LOSS,
            StatementType.CASH_FLOW,
        ):
            referenced_notes.update(p.note_numbers)

    all_notes: list[dict[str, str]] = []
    current_note_num: str = ""
    current_note_title: str = ""
    current_note_content: list[str] = []
    current_note_start_page: int = 0

    with pdfplumber.open(pdf_path) as pdf:
        for page_num in footnote_pages:
            if page_num < 1 or page_num > len(pdf.pages):
                continue

            page = pdf.pages[page_num - 1]
            text = page.extract_text() or ""

            if not text.strip():
                continue

            lines = text.strip().split("\n")

            for line in lines:
                line = line.strip()
                if not line:
                    continue

                # Skip page headers/footers
                if _is_header_footer(line):
                    continue

                # Check if this line starts a new note
                note_match = re.match(
                    r"^(?:Note\s+)?(\d{1,2})[.\s:)\-]+\s*(.+)",
                    line,
                    re.IGNORECASE,
                )

                if note_match:
                    # Save previous note
                    if current_note_num:
                        all_notes.append(
                            {
                                "Note_Number": current_note_num,
                                "Note_Title": current_note_title,
                                "Content": "\n".join(current_note_content).strip(),
                                "Source_Page": str(current_note_start_page),
                            }
                        )

                    current_note_num = note_match.group(1)
                    current_note_title = note_match.group(2).strip()
                    current_note_content = []
                    current_note_start_page = page_num
                else:
                    # Check for sub-note patterns like "2.1", "3a", etc.
                    sub_note_match = re.match(r"^(\d{1,2}\.\d{1,2})\s+(.+)", line)
                    if sub_note_match and current_note_num:
                        current_note_content.append(line)
                    elif current_note_num:
                        current_note_content.append(line)

    # Save last note
    if current_note_num:
        all_notes.append(
            {
                "Note_Number": current_note_num,
                "Note_Title": current_note_title,
                "Content": "\n".join(current_note_content).strip(),
                "Source_Page": str(current_note_start_page),
            }
        )

    if not all_notes:
        return pd.DataFrame()

    df = pd.DataFrame(all_notes)

    if verbose:
        print(f"  Extracted {len(df)} footnotes for {scope.value}")

    return df


def _is_header_footer(line: str) -> bool:
    """Check if a line is a page header or footer."""
    lower = line.lower().strip()

    # Common header/footer patterns
    patterns = [
        r"^annual\s+report",
        r"^integrated\s+annual\s+report",
        r"^\d+\s*$",  # Just a page number
        r"^(laurus|tata|infosys|reliance)\s+labs?\s+(limited)?",
        r"notes\s+to\s+(?:the\s+)?(?:standalone\s+|consolidated\s+)?financial\s+statements\s+for",
        r"^\(all\s+amounts\s+in",
        r"^financial\s+statements\s*$",
    ]

    for pattern in patterns:
        if re.match(pattern, lower, re.IGNORECASE):
            return True

    return False


def extract_footnotes_for_statement(
    footnotes_df: pd.DataFrame,
    note_numbers: list[int],
) -> pd.DataFrame:
    """
    Filter footnotes to only those referenced by a specific statement.
    """
    if footnotes_df.empty or not note_numbers:
        return pd.DataFrame()

    note_strs = [str(n) for n in note_numbers]
    filtered = footnotes_df[footnotes_df["Note_Number"].isin(note_strs)]
    return filtered.reset_index(drop=True)
