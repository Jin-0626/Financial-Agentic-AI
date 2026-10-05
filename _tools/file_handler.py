"""File handler for parsing Excel/CSV files and preparing them for analysis.

Provides smart routing: small files are parsed directly and content is
returned as text for LLM analysis; large files are uploaded to sandbox
and code templates are generated for the agent to execute.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

CONTENT_THRESHOLD = 50_000


@dataclass
class ParsedFile:
    filename: str
    file_type: str
    row_count: int
    columns: list[str]
    preview: str
    is_small: bool
    full_content: Optional[str] = None


class FileHandler:
    """Handles file parsing and preparation for AI analysis."""

    SUPPORTED_EXTENSIONS = {".csv", ".xlsx", ".xls", ".tsv", ".txt"}

    @staticmethod
    def _detect_type(filename: str) -> Optional[str]:
        filename_lower = filename.lower()
        for ext in [".csv", ".tsv", ".txt", ".xlsx", ".xls"]:
            if filename_lower.endswith(ext):
                return ext.lstrip(".")
        return None

    @staticmethod
    def _parse_csv(content: bytes, filename: str) -> pd.DataFrame:
        for encoding in ["utf-8", "gbk", "latin-1"]:
            try:
                df = pd.read_csv(io.BytesIO(content), encoding=encoding)
                return df
            except (UnicodeDecodeError, UnicodeError):
                continue
        return pd.read_csv(io.BytesIO(content), encoding="utf-8", errors="ignore")

    @staticmethod
    def _parse_excel(content: bytes) -> pd.DataFrame:
        return pd.read_excel(io.BytesIO(content), engine="openpyxl")

    @classmethod
    def parse(cls, filename: str, content: bytes) -> ParsedFile:
        """Parse a file and return structured data.

        Args:
            filename: Original filename with extension.
            content: Raw file content as bytes.

        Returns:
            ParsedFile with metadata and preview data.
        """
        file_type = cls._detect_type(filename)
        if file_type is None:
            raise ValueError(f"Unsupported file type: {filename}. Supported types: CSV, Excel (.xlsx/.xls), TXT")

        try:
            if file_type in ("csv", "tsv", "txt"):
                df = cls._parse_csv(content, filename)
            elif file_type in ("xlsx", "xls"):
                df = cls._parse_excel(content)
            else:
                raise ValueError(f"Unsupported file type: {file_type}")

            row_count = len(df)
            columns = list(df.columns)

            preview_rows = min(5, row_count)
            preview_df = df.head(preview_rows)
            preview = (
                f"File: {filename}\n"
                f"Type: {file_type}\n"
                f"Rows: {row_count}\n"
                f"Columns: {len(columns)}\n"
                f"Column Names: {columns}\n\n"
                f"First {preview_rows} Rows Preview:\n{preview_df.to_string(index=False)}"
            )

            full_content = None
            is_small = True

            if row_count <= 100:
                full_df = df.head(100)
                full_content = f"{preview}\n\nFull Data (First 100 Rows):\n{full_df.to_string(index=False)}"
                is_small = True
            else:
                is_small = False
                full_content = None

            return ParsedFile(
                filename=filename,
                file_type=file_type,
                row_count=row_count,
                columns=columns,
                preview=preview,
                is_small=is_small,
                full_content=full_content,
            )

        except Exception as e:
            logger.error(f"Failed to parse file {filename}: {e}")
            raise ValueError(f"Failed to parse file: {str(e)}")

    @classmethod
    def generate_code_for_large_file(cls, sandbox_filename: str, file_type: str, columns: list[str]) -> str:
        """Generate Python code template for analyzing large files in sandbox.

        Uses Python's built-in csv module (the sandbox does not have pandas).

        Args:
            sandbox_filename: Filename as stored in sandbox (with file_id prefix).
            file_type: File extension type (csv, xlsx, etc.).
            columns: List of column names.

        Returns:
            Python code string ready for sandbox execution.
        """
        filepath = f"/workspace/{sandbox_filename}"

        code = f'''import csv, json

            filepath = "{filepath}"
            
            with open(filepath, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            
            # Basic statistics
            stats = {{
                "row_count": len(rows),
                "columns": list(rows[0].keys()) if rows else [],
            }}
            
            # Missing value statistics
            if rows:
                missing = {{}}
                for col in rows[0].keys():
                    nulls = sum(1 for r in rows if not r.get(col))
                    if nulls > 0:
                        missing[col] = nulls
                if missing:
                    stats["missing_values"] = missing
            
            print(json.dumps(stats, ensure_ascii=False, indent=2))'''

        return code