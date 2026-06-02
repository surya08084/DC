from __future__ import annotations
import logging
import re
from pathlib import Path
from typing import Optional
import pandas as pd

logger = logging.getLogger(__name__)


class PDFParser:
    """
    Parse state directory PDFs into structured DataFrames.
    Tries pdfplumber first (structured table PDFs), falls back to pytesseract OCR.
    """

    def extract_tables(self, path: str | Path) -> Optional[pd.DataFrame]:
        path = Path(path)
        df = self._try_pdfplumber(path)
        if df is not None and not df.empty:
            return df
        logger.info("PDFParser: pdfplumber returned no tables, trying OCR — %s", path.name)
        return self._try_ocr(path)

    def _try_pdfplumber(self, path: Path) -> Optional[pd.DataFrame]:
        try:
            import pdfplumber
            frames: list[pd.DataFrame] = []
            with pdfplumber.open(path) as pdf:
                for page in pdf.pages:
                    tables = page.extract_tables()
                    for table in tables:
                        if not table or len(table) < 2:
                            continue
                        headers = [str(h).strip().lower().replace(" ", "_") for h in table[0]]
                        rows = table[1:]
                        frames.append(pd.DataFrame(rows, columns=headers))
            return pd.concat(frames, ignore_index=True) if frames else None
        except ImportError:
            logger.warning("PDFParser: pdfplumber not installed")
            return None
        except Exception as e:
            logger.warning("PDFParser: pdfplumber failed — %s", e)
            return None

    def _try_ocr(self, path: Path) -> Optional[pd.DataFrame]:
        try:
            from pdf2image import convert_from_path
            import pytesseract
            images = convert_from_path(str(path))
            rows: list[dict] = []
            for img in images:
                text = pytesseract.image_to_string(img)
                for line in text.splitlines():
                    line = line.strip()
                    if line:
                        rows.append({"raw_text": line})
            return pd.DataFrame(rows) if rows else None
        except ImportError:
            logger.warning("PDFParser: pytesseract/pdf2image not installed")
            return None
        except Exception as e:
            logger.warning("PDFParser: OCR failed — %s", e)
            return None

    def extract_product_names(self, path: str | Path) -> list[str]:
        """Quick extraction of all product name strings from a directory PDF."""
        df = self.extract_tables(path)
        if df is None:
            return []
        name_cols = [c for c in df.columns if any(k in c for k in ["name", "product", "brand", "raw"])]
        if not name_cols:
            return []
        return df[name_cols[0]].dropna().astype(str).tolist()
