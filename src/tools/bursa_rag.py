import logging
import re
from datetime import date
from typing import TypedDict

import asyncpg
from langchain_core.tools import tool
from pydantic import BaseModel, Field
from pypdf import PdfReader

from src.config import default_config
from src.ollama_runtime import build_ollama_embeddings

logger = logging.getLogger(__name__)

BALANCE_SHEET_QUERY_TERMS = (
    "balance sheet",
    "financial position",
    "assets",
    "liabilities",
    "equity",
    "borrowings",
    "lease liabilities",
    "cash and cash equivalents",
)

BALANCE_SHEET_CONTENT_PATTERNS = (
    "%statement%financial position%",
    "%statements%financial position%",
    "%total assets%",
    "%total liabilities%",
    "%total equity%",
    "%equity and liabilities%",
    "%non-current liabilities%",
    "%current liabilities%",
    "%lease liabilities%",
    "%cash and cash equivalents%",
)


class BursaAnnouncementChunk(TypedDict):
    chunk: str
    section: str
    quarter: str
    quarter_ended: str
    similarity: float


def _asyncpg_database_url(database_url: str) -> str:
    """Convert a SQLAlchemy async URL to the scheme accepted by asyncpg."""
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


embeddings_client = build_ollama_embeddings()


def extract_text_from_pdf(pdf_path: str) -> str:
    """Extract raw text from PDF using pypdf."""
    reader = PdfReader(pdf_path)
    pages_text = []
    for page in reader.pages:
        text = page.extract_text() or ""
        # Clean excessive whitespace and carriage returns
        text = re.sub(r"\n\s*\n", "\n\n", text)
        pages_text.append(text)
    return "\n".join(pages_text)


def classify_section(chunk_text: str) -> str:
    """Classify the chunk into standard Bursa Appendix 9B categories."""
    lower_chunk = chunk_text.lower()
    if (
        "statement of financial position" in lower_chunk
        or "statements of financial position" in lower_chunk
        or "total assets" in lower_chunk
        or "equity and liabilities" in lower_chunk
    ):
        return "Balance Sheet - Financial Position"
    if "prospects" in lower_chunk or "current year prospects" in lower_chunk:
        return "Part B - Prospects"
    if "borrowings" in lower_chunk or "debt securities" in lower_chunk:
        return "Part B - Borrowings & Gearing"
    if "dividend" in lower_chunk:
        return "Part B - Dividends"
    if "mfrs 16" in lower_chunk or "leases" in lower_chunk:
        return "Part A - MFRS 16 Leases"
    return "General Notes"


def chunk_text(text: str, chunk_size: int = 900, overlap: int = 150) -> list[str]:
    """Split text into overlapping windows of at most ``chunk_size`` characters."""
    if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("chunk_size must be positive and overlap must be smaller than chunk_size")

    normalized_text = re.sub(r"\s+", " ", text).strip()
    if not normalized_text:
        return []

    step = chunk_size - overlap
    return [
        normalized_text[start : start + chunk_size]
        for start in range(0, len(normalized_text), step)
    ]


async def ingest_bursa_pdf(
    pdf_path: str,
    stock_code: str,
    company_name: str,
    fiscal_quarter: str,
    quarter_ended: date,
) -> int:
    """
    Parse a quarterly PDF announcement, embed with Ollama, and persist to pgvector.
    Returns count of chunks stored.
    """
    logger.info("Extracting text from: %s", pdf_path)
    raw_text = extract_text_from_pdf(pdf_path)

    chunks = chunk_text(raw_text)
    if not chunks:
        logger.warning("No text extracted from PDF: %s", pdf_path)
        return 0

    logger.info("Generating embeddings for %d chunks via Ollama Cloud...", len(chunks))
    # Batch embed documents
    vectors = await embeddings_client.aembed_documents(chunks)

    # Ingest into PostgreSQL via asyncpg
    conn = await asyncpg.connect(_asyncpg_database_url(str(default_config["database_url"])))
    try:
        query = """
            INSERT INTO bursa_announcements (
                stock_code, company_name, fiscal_quarter, 
                quarter_ended, section_category, content_chunk, embedding
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7::vector)
        """
        records = [
            (
                stock_code,
                company_name,
                fiscal_quarter,
                quarter_ended,
                classify_section(chunk),
                chunk,
                str(vector),
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]

        await conn.executemany(query, records)
        logger.info("Successfully ingested %d chunks for %s (%s)", len(records), company_name, stock_code)
        return len(records)
    finally:
        await conn.close()

class BursaQueryInput(BaseModel):
    stock_code: str = Field(description="4-digit Bursa stock code, e.g. '0157'")
    query: str = Field(description="Semantic query focusing on prospects, debt, leases, or dividends")


@tool(args_schema=BursaQueryInput)
async def get_bursa_quarterly_notes(stock_code: str, query: str) -> str:
    """Retrieve semantic chunks and notes from indexed Bursa quarterly PDF reports."""
    results = await search_bursa_notes(stock_code=stock_code, query=query, limit=3)
    if not results:
        return f"No disclosures found for stock code {stock_code}."
    
    formatted = []
    for r in results:
        formatted.append(f"[{r['section']} | {r['quarter']}]\n{r['chunk']}")
    return "\n\n---\n\n".join(formatted)

async def search_bursa_notes(
    stock_code: str, query: str, limit: int = 4
) -> list[BursaAnnouncementChunk]:
    """
    Hybrid retrieval against indexed Bursa quarterly disclosures.

    Vector search handles broad semantic requests. Balance-sheet queries also get
    a lexical pass because tabular statements often embed important labels like
    TOTAL ASSETS or lease liabilities that can rank poorly in embedding search.
    """
    query_embedding = await embeddings_client.aembed_query(query)
    conn = await asyncpg.connect(_asyncpg_database_url(str(default_config["database_url"])))
    try:
        sql = """
            SELECT content_chunk, section_category, fiscal_quarter, quarter_ended,
                   1 - (embedding <=> $1::vector) AS similarity_score
            FROM bursa_announcements
            WHERE stock_code = $2
            ORDER BY embedding <=> $1::vector
            LIMIT $3;
        """
        rows = await conn.fetch(sql, str(query_embedding), stock_code, limit)
        if any(term in query.lower() for term in BALANCE_SHEET_QUERY_TERMS):
            lexical_sql = """
                SELECT content_chunk, section_category, fiscal_quarter, quarter_ended,
                       1.0 AS similarity_score
                FROM bursa_announcements
                WHERE stock_code = $1
                  AND content_chunk ILIKE ANY($2::text[])
                ORDER BY quarter_ended DESC
                LIMIT $3;
            """
            rows = [*rows, *await conn.fetch(
                lexical_sql,
                stock_code,
                list(BALANCE_SHEET_CONTENT_PATTERNS),
                max(limit, 4),
            )]

        seen_chunks = set()
        deduped_rows = []
        for row in rows:
            chunk = row["content_chunk"]
            if chunk in seen_chunks:
                continue
            seen_chunks.add(chunk)
            deduped_rows.append(row)

        return [
            {
                "chunk": r["content_chunk"],
                "section": r["section_category"],
                "quarter": r["fiscal_quarter"],
                "quarter_ended": r["quarter_ended"].isoformat(),
                "similarity": round(float(r["similarity_score"]), 3),
            }
            for r in deduped_rows
        ]
        
        
    finally:
        await conn.close()
