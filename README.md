# Annual Report PDF Extractor

A Python tool that extracts key financial statements from Indian company annual report PDFs and outputs them as CSV files.

## What It Extracts

For each PDF, the tool identifies and extracts:

1. **Balance Sheet** — Assets, liabilities, and equity (standalone & consolidated)
2. **Statement of Profit and Loss** — Revenue, expenses, and net profit (standalone & consolidated)
3. **Statement of Cash Flows** — Operating, investing, and financing activities (standalone & consolidated)
4. **Footnotes** — Notes to financial statements referenced by the above tables

## Output Format

Each extracted statement is saved as a separate CSV file in the output directory:

```
output/
├── CompanyName_Standalone_Balance_Sheet.csv
├── CompanyName_Standalone_Profit_and_Loss.csv
├── CompanyName_Standalone_Cash_Flow.csv
├── CompanyName_Standalone_Footnotes.csv
├── CompanyName_Consolidated_Balance_Sheet.csv
├── CompanyName_Consolidated_Profit_and_Loss.csv
├── CompanyName_Consolidated_Cash_Flow.csv
└── CompanyName_Consolidated_Footnotes.csv
```

## Installation

```bash
pip install -r requirements.txt
```

## Usage

### Extract from a single PDF:

```bash
python -m src.main path/to/annual_report.pdf -o output/
```

### Extract from multiple PDFs:

```bash
python -m src.main report1.pdf report2.pdf -o output/
```

### Options:

```
-o, --output-dir    Output directory for CSVs (default: ./output)
-v, --verbose       Enable verbose logging
--standalone-only   Only extract standalone statements
--consolidated-only Only extract consolidated statements
```

## Supported Report Formats

- Indian Ind AS format annual reports
- Both standalone and consolidated financial statements
- Reports with standard sections: Balance Sheet, P&L, Cash Flow, Notes

## How It Works

1. **Page Classification** — Scans all pages and classifies them by financial statement type using keyword matching and structural analysis
2. **Section Detection** — Identifies standalone vs consolidated sections, and locates the notes/footnotes pages
3. **Table Extraction** — Uses pdfplumber's table detection to extract structured tabular data from financial statement pages
4. **Text Parsing** — Falls back to intelligent text parsing when table detection fails (common with Indian annual reports)
5. **Note Linking** — Extracts footnotes referenced in the main statements and links them by note number
6. **CSV Output** — Cleans and formats extracted data into well-structured CSV files
