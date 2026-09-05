import asyncio
import sys
from datetime import date

from agents.tools.bursa_rag import ingest_bursa_pdf


async def main():
    if len(sys.argv) < 6:
        print(
            "Usage: python ingest_report.py <pdf_path> <stock_code> <company_name> <fiscal_quarter> <YYYY-MM-DD>"
        )
        sys.exit(1)

    pdf_path = sys.argv[1]
    stock_code = sys.argv[2]
    company_name = sys.argv[3]
    fiscal_quarter = sys.argv[4]
    quarter_ended = date.fromisoformat(sys.argv[5])

    print(f"[*] Parsing and vectorizing: {pdf_path}")
    chunks_stored = await ingest_bursa_pdf(
        pdf_path=pdf_path,
        stock_code=stock_code,
        company_name=company_name,
        fiscal_quarter=fiscal_quarter,
        quarter_ended=quarter_ended,
    )
    print(
        f"[+] Ingestion complete! Stored {chunks_stored} vectorized chunks in pgvector."
    )


if __name__ == "__main__":
    asyncio.run(main())
