import logging
import re
from datetime import date
from typing import TypedDict

import asyncpg
from langchain_core.tools import tool
from pydantic import BaseModel, Field
from pypdf import PdfReader

from agents.config import default_config
from agents.ollama_runtime import build_ollama_embeddings
from agents.research_planning import CompanyRegistryEntry

logger = logging.getLogger(__name__)

FINANCIAL_STATEMENT_SECTIONS = (
    "Comprehensive Income Statement",
    "Financial Position Statement",
    "Changes In Equity Statement",
    "Cash Flow Statement",
)

SECTION_HEADING_PATTERNS = (
    (
        "Comprehensive Income Statement",
        re.compile(
            r"(?m)^\s*CONDENSED\s+CONSOLIDATED\s+STATEMENTS?\s+OF\s+COMPREHENSIVE\s+INCOME",
        ),
    ),
    (
        "Financial Position Statement",
        re.compile(
            r"(?m)^\s*CONDENSED\s+CONSOLIDATED\s+STATEMENTS?\s+OF\s+FINANCIAL\s+POSITION",
        ),
    ),
    (
        "Changes In Equity Statement",
        re.compile(
            r"(?m)^\s*CONDENSED\s+CONSOLIDATED\s+STATEMENTS?\s+OF\s+CHANGES\s+IN\s+EQUITY",
        ),
    ),
    (
        "Cash Flow Statement",
        re.compile(
            r"(?m)^\s*CONDENSED\s+CONSOLIDATED\s+STATEMENTS?\s+OF\s+CASH\s+FLOWS?",
        ),
    ),
    (
        "Part A - Explanatory Notes",
        re.compile(r"\bPart\s+A\s+-\s+Explanatory\s+notes\b", re.IGNORECASE),
    ),
    (
        "Part B - Additional Information",
        re.compile(r"\bPart\s+B\b", re.IGNORECASE),
    ),
)

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

FINANCIAL_STATEMENT_QUERY_TERMS = (
    "financial statement",
    "financial statements",
    "comprehensive income",
    "income statement",
    "profit or loss",
    "financial position",
    "balance sheet",
    "cash flow",
    "cash flows",
    "changes in equity",
    "full quarter",
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
        text = re.sub(r"[ \t]+", " ", text)
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
        return "Financial Position Statement"
    if "statement of comprehensive income" in lower_chunk:
        return "Comprehensive Income Statement"
    if "statement of changes in equity" in lower_chunk:
        return "Changes In Equity Statement"
    if "statement of cash flows" in lower_chunk or "statements of cash flows" in lower_chunk:
        return "Cash Flow Statement"
    if "prospects" in lower_chunk or "current year prospects" in lower_chunk:
        return "Part B - Prospects"
    if "borrowings" in lower_chunk or "debt securities" in lower_chunk:
        return "Part B - Borrowings & Gearing"
    if "dividend" in lower_chunk:
        return "Part B - Dividends"
    if "mfrs 16" in lower_chunk or "leases" in lower_chunk:
        return "Part A - MFRS 16 Leases"
    return "General Notes"


def _normalize_chunk(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.splitlines()]
    compact_lines = [line for line in lines if line]
    return "\n".join(compact_lines).strip()


def _find_report_sections(text: str) -> list[tuple[str, int, int]]:
    matches = []
    for section_name, pattern in SECTION_HEADING_PATTERNS:
        for match in pattern.finditer(text):
            matches.append((match.start(), section_name))
    matches.sort(key=lambda item: item[0])

    sections = []
    for index, (start, section_name) in enumerate(matches):
        end = matches[index + 1][0] if index + 1 < len(matches) else len(text)
        if end > start:
            sections.append((section_name, start, end))

    merged = []
    for section_name, start, end in sections:
        if (
            merged
            and section_name in FINANCIAL_STATEMENT_SECTIONS
            and merged[-1][0] == section_name
        ):
            previous_name, previous_start, _ = merged[-1]
            merged[-1] = (previous_name, previous_start, end)
        else:
            merged.append((section_name, start, end))
    return merged


def chunk_bursa_report_text(
    text: str,
    fallback_chunk_size: int = 1800,
    fallback_overlap: int = 250,
) -> list[tuple[str, str]]:
    """
    Split a Bursa interim report into semantically coherent chunks.

    Financial statements are stored as full statement sections so table headings,
    rows, and period columns stay together during retrieval. Narrative sections
    still use overlapping chunks to preserve recall without exploding context.
    """
    sections = _find_report_sections(text)
    if not sections:
        return [
            (classify_section(chunk), chunk)
            for chunk in chunk_text(text, fallback_chunk_size, fallback_overlap)
        ]

    section_chunks: list[tuple[str, str]] = []
    covered_ranges = []
    for section_name, start, end in sections:
        section_text = _normalize_chunk(text[start:end])
        if not section_text:
            continue
        covered_ranges.append((start, end))
        if section_name in FINANCIAL_STATEMENT_SECTIONS:
            section_chunks.append((section_name, section_text))
        else:
            for chunk in chunk_text(section_text, fallback_chunk_size, fallback_overlap):
                section_chunks.append((section_name, chunk))

    # Preserve preamble text if present, but avoid duplicating statement sections.
    cursor = 0
    narrative_chunks: list[tuple[str, str]] = []
    for start, end in covered_ranges:
        if cursor < start:
            narrative = _normalize_chunk(text[cursor:start])
            narrative_chunks.extend(
                (classify_section(chunk), chunk)
                for chunk in chunk_text(narrative, fallback_chunk_size, fallback_overlap)
            )
        cursor = max(cursor, end)
    if cursor < len(text):
        narrative = _normalize_chunk(text[cursor:])
        narrative_chunks.extend(
            (classify_section(chunk), chunk)
            for chunk in chunk_text(narrative, fallback_chunk_size, fallback_overlap)
        )

    return [*narrative_chunks, *section_chunks]


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

    chunks = chunk_bursa_report_text(raw_text)
    if not chunks:
        logger.warning("No text extracted from PDF: %s", pdf_path)
        return 0

    logger.info("Generating embeddings for %d chunks via Ollama Cloud...", len(chunks))
    # Batch embed documents
    chunk_texts = [chunk for _, chunk in chunks]
    vectors = await embeddings_client.aembed_documents(chunk_texts)

    # Ingest into PostgreSQL via asyncpg
    conn = await asyncpg.connect(_asyncpg_database_url(str(default_config["database_url"])))
    try:
        await conn.execute(
            """
            DELETE FROM bursa_announcements
            WHERE stock_code = $1
              AND fiscal_quarter = $2
              AND quarter_ended = $3
            """,
            stock_code,
            fiscal_quarter,
            quarter_ended,
        )
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
                section,
                chunk,
                str(vector),
            )
            for (section, chunk), vector in zip(chunks, vectors, strict=True)
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
        if any(term in query.lower() for term in FINANCIAL_STATEMENT_QUERY_TERMS):
            statement_rows = await conn.fetch(
                """
                SELECT content_chunk, section_category, fiscal_quarter, quarter_ended,
                       1.0 AS similarity_score
                FROM bursa_announcements
                WHERE stock_code = $1
                  AND section_category = ANY($2::text[])
                  AND quarter_ended = (
                      SELECT max(quarter_ended)
                      FROM bursa_announcements
                      WHERE stock_code = $1
                  )
                ORDER BY
                    CASE section_category
                        WHEN 'Comprehensive Income Statement' THEN 1
                        WHEN 'Financial Position Statement' THEN 2
                        WHEN 'Changes In Equity Statement' THEN 3
                        WHEN 'Cash Flow Statement' THEN 4
                        ELSE 5
                    END;
                """,
                stock_code,
                list(FINANCIAL_STATEMENT_SECTIONS),
            )
            rows = [*statement_rows, *rows]
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


async def get_latest_financial_statement_sections(
    stock_code: str,
) -> list[BursaAnnouncementChunk]:
    """Fetch full financial-statement sections for the latest indexed quarter."""
    conn = await asyncpg.connect(_asyncpg_database_url(str(default_config["database_url"])))
    try:
        rows = await conn.fetch(
            """
            SELECT content_chunk, section_category, fiscal_quarter, quarter_ended,
                   1.0 AS similarity_score
            FROM bursa_announcements
            WHERE stock_code = $1
              AND section_category = ANY($2::text[])
              AND quarter_ended = (
                  SELECT max(quarter_ended)
                  FROM bursa_announcements
                  WHERE stock_code = $1
              )
            ORDER BY
                CASE section_category
                    WHEN 'Comprehensive Income Statement' THEN 1
                    WHEN 'Financial Position Statement' THEN 2
                    WHEN 'Changes In Equity Statement' THEN 3
                    WHEN 'Cash Flow Statement' THEN 4
                    ELSE 5
                END;
            """,
            stock_code,
            list(FINANCIAL_STATEMENT_SECTIONS),
        )
        return [
            {
                "chunk": r["content_chunk"],
                "section": r["section_category"],
                "quarter": r["fiscal_quarter"],
                "quarter_ended": r["quarter_ended"].isoformat(),
                "similarity": round(float(r["similarity_score"]), 3),
            }
            for r in rows
        ]
    finally:
        await conn.close()


async def list_indexed_bursa_companies() -> list[CompanyRegistryEntry]:
    """
    Return companies represented in the local filing index.

    This is the most reliable local company universe available when no separate
    Bursa master list has been configured.
    """
    conn = await asyncpg.connect(_asyncpg_database_url(str(default_config["database_url"])))
    try:
        rows = await conn.fetch(
            """
            SELECT stock_code, company_name, max(quarter_ended) AS latest_quarter_ended
            FROM bursa_announcements
            GROUP BY stock_code, company_name
            ORDER BY stock_code, company_name;
            """
        )
        return [
            CompanyRegistryEntry(
                stock_code=row["stock_code"],
                company_name=row["company_name"],
            )
            for row in rows
        ]
    finally:
        await conn.close()
