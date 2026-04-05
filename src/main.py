"""
Annual Report PDF Extractor - Main Module

Extracts Balance Sheet, Profit & Loss, Cash Flow statements, and their
footnotes from Indian company annual report PDFs into CSV format.
"""

import argparse
import os
import re
import sys
from pathlib import Path


from src.page_classifier import (
    SectionScope,
    StatementType,
    classify_pages,
    get_statement_page_ranges,
)
from src.table_extractor import extract_table_from_pages
from src.footnote_extractor import extract_footnotes


def extract_company_name(pdf_path: str) -> str:
    """Extract company name from PDF filename or content."""
    filename = Path(pdf_path).stem

    # Try to extract from common filename patterns
    # e.g., "AR_20015_LAURUSLABS_2021_2022_06062022174222"
    # e.g., "Annual_Report_2024-25"
    parts = filename.replace("-", "_").split("_")

    # Look for company name-like parts (not dates, not generic words)
    skip_words = {
        "ar",
        "annual",
        "report",
        "integrated",
        "financial",
        "statements",
        "pdf",
        "the",
        "of",
        "and",
        "for",
    }

    name_parts = []
    for part in parts:
        lower = part.lower()
        if lower in skip_words:
            continue
        if re.match(r"^\d+$", part):
            continue
        if len(part) > 2:
            name_parts.append(part)

    if name_parts:
        return "_".join(name_parts)

    # Fallback: use full filename
    return re.sub(r"[^\w]", "_", filename)


def process_pdf(
    pdf_path: str,
    output_dir: str,
    verbose: bool = False,
    standalone_only: bool = False,
    consolidated_only: bool = False,
) -> list[str]:
    """
    Process a single annual report PDF and extract financial statements to CSV.

    Returns a list of generated CSV file paths.
    """
    print(f"\nProcessing: {pdf_path}")
    print("=" * 60)

    # Step 1: Classify pages
    print("\n[1/4] Classifying pages...")
    pages = classify_pages(pdf_path, verbose=verbose)

    if not pages:
        print("  WARNING: No financial statement pages found!")
        return []

    # Count pages by type
    type_counts: dict[str, int] = {}
    for p in pages:
        key = f"{p.scope.value} {p.statement_type.value}"
        type_counts[key] = type_counts.get(key, 0) + 1

    for key, count in sorted(type_counts.items()):
        print(f"  Found {count} page(s): {key}")

    # Step 2: Determine scopes to process
    scopes_to_process: list[SectionScope] = []
    if standalone_only:
        scopes_to_process = [SectionScope.STANDALONE]
    elif consolidated_only:
        scopes_to_process = [SectionScope.CONSOLIDATED]
    else:
        available_scopes = set(p.scope for p in pages)
        if SectionScope.STANDALONE in available_scopes:
            scopes_to_process.append(SectionScope.STANDALONE)
        if SectionScope.CONSOLIDATED in available_scopes:
            scopes_to_process.append(SectionScope.CONSOLIDATED)
        if not scopes_to_process and SectionScope.UNKNOWN in available_scopes:
            scopes_to_process.append(SectionScope.UNKNOWN)

    # Step 3: Extract tables
    print("\n[2/4] Extracting financial statements...")
    page_ranges = get_statement_page_ranges(pages)
    company_name = extract_company_name(pdf_path)
    generated_files: list[str] = []

    statement_types = [
        StatementType.BALANCE_SHEET,
        StatementType.PROFIT_AND_LOSS,
        StatementType.CASH_FLOW,
    ]

    for scope in scopes_to_process:
        print(f"\n  --- {scope.value} Statements ---")

        for st_type in statement_types:
            key = (scope, st_type)
            page_nums = page_ranges.get(key, [])

            if not page_nums:
                print(f"  {st_type.value}: No pages found, skipping")
                continue

            print(f"  {st_type.value}: Extracting from page(s) {page_nums}")

            table = extract_table_from_pages(
                pdf_path, page_nums, st_type, scope.value, verbose=verbose
            )

            if table and not table.data.empty:
                filename = f"{company_name}_{scope.value}_{st_type.value}.csv"
                filepath = os.path.join(output_dir, filename)
                table.data.to_csv(filepath, index=False)
                generated_files.append(filepath)
                print(f"    -> Saved: {filename} ({len(table.data)} rows)")
            else:
                print(f"    -> WARNING: No data extracted for {st_type.value}")

    # Step 4: Extract footnotes
    print("\n[3/4] Extracting footnotes...")

    for scope in scopes_to_process:
        footnotes_df = extract_footnotes(pdf_path, pages, scope, verbose=verbose)

        if not footnotes_df.empty:
            filename = f"{company_name}_{scope.value}_Footnotes.csv"
            filepath = os.path.join(output_dir, filename)
            footnotes_df.to_csv(filepath, index=False)
            generated_files.append(filepath)
            print(f"  -> Saved: {filename} ({len(footnotes_df)} notes)")
        else:
            print(f"  -> No footnotes extracted for {scope.value}")

    # Summary
    print("\n[4/4] Summary")
    print(f"  Generated {len(generated_files)} CSV file(s) in: {output_dir}")
    for f in generated_files:
        print(f"    - {os.path.basename(f)}")

    return generated_files


def main() -> None:
    """Main entry point for CLI usage."""
    parser = argparse.ArgumentParser(
        description="Extract financial statements from annual report PDFs into CSV format.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m src.main report.pdf -o output/
  python -m src.main report1.pdf report2.pdf -o output/ -v
  python -m src.main report.pdf --consolidated-only
        """,
    )
    parser.add_argument(
        "pdfs",
        nargs="+",
        help="Path(s) to annual report PDF file(s)",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        default="./output",
        help="Output directory for CSV files (default: ./output)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    parser.add_argument(
        "--standalone-only",
        action="store_true",
        help="Only extract standalone statements",
    )
    parser.add_argument(
        "--consolidated-only",
        action="store_true",
        help="Only extract consolidated statements",
    )

    args = parser.parse_args()

    if args.standalone_only and args.consolidated_only:
        print("ERROR: Cannot use both --standalone-only and --consolidated-only")
        sys.exit(1)

    # Create output directory
    os.makedirs(args.output_dir, exist_ok=True)

    all_files: list[str] = []
    for pdf_path in args.pdfs:
        if not os.path.exists(pdf_path):
            print(f"ERROR: File not found: {pdf_path}")
            continue
        if not pdf_path.lower().endswith(".pdf"):
            print(f"WARNING: Skipping non-PDF file: {pdf_path}")
            continue

        files = process_pdf(
            pdf_path,
            args.output_dir,
            verbose=args.verbose,
            standalone_only=args.standalone_only,
            consolidated_only=args.consolidated_only,
        )
        all_files.extend(files)

    print(f"\n{'=' * 60}")
    print(f"Total: Generated {len(all_files)} CSV file(s)")
    if not all_files:
        print("No data was extracted. Please check the PDF format.")
        sys.exit(1)


if __name__ == "__main__":
    main()
