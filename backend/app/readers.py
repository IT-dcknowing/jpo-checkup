"""Lecture des fichiers déposés (CSV, XLSX, PDF natif ou numérisé) → texte brut."""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

import chardet

ALLOWED_EXT = {".csv", ".xlsx", ".pdf"}
OCR_MIN_CHARS_PER_PAGE = 40
MAX_PDF_PAGES = 80
MAX_ROWS_PER_SHEET = 20_000


class ReaderError(Exception):
    """Fichier illisible ou non conforme à son extension."""


@dataclass
class ReadResult:
    text: str
    kind: str
    warnings: list[str] = field(default_factory=list)


def sniff_kind(head: bytes, ext: str) -> str:
    """Vérifie que le contenu correspond à l'extension (pas de confiance au nom de fichier)."""
    if ext == ".pdf":
        if not head.startswith(b"%PDF"):
            raise ReaderError("le fichier n'est pas un vrai PDF")
        return "pdf"
    if ext == ".xlsx":
        if not head.startswith(b"PK"):
            raise ReaderError("le fichier n'est pas un vrai classeur Excel (.xlsx)")
        return "xlsx"
    if ext == ".csv":
        if b"\x00" in head[:2048] or head.startswith((b"PK", b"%PDF")):
            raise ReaderError("le fichier n'est pas un vrai CSV")
        return "csv"
    raise ReaderError("format non accepté (CSV, XLSX ou PDF uniquement)")


def _cell(x) -> str:
    if x is None:
        return ""
    if isinstance(x, float):
        if x != x:  # NaN
            return ""
        return str(int(x)) if x == int(x) else str(round(x, 4))
    return re.sub(r"\s+", " ", str(x)).strip()


def _rows_to_lines(rows) -> list[str]:
    lines = []
    for row in rows:
        cells = [_cell(c) for c in row]
        if any(cells):
            lines.append(" | ".join(cells).rstrip(" |"))
    return lines


def read_csv(path: Path) -> ReadResult:
    raw = path.read_bytes()
    enc = (chardet.detect(raw[:200_000]).get("encoding") or "utf-8")
    if enc.lower() in ("ascii",):
        enc = "utf-8"
    try:
        text = raw.decode(enc)
    except (UnicodeDecodeError, LookupError):
        text, enc = raw.decode("cp1252", errors="replace"), "cp1252"
    sample = text[:20_000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
    except csv.Error:
        dialect = csv.excel_tab if "\t" in sample else csv.excel
        dialect.delimiter = ";" if sample.count(";") >= sample.count(",") else ","
    rows = csv.reader(io.StringIO(text), dialect)
    lines = _rows_to_lines(row for _, row in zip(range(MAX_ROWS_PER_SHEET), rows))
    if not lines:
        raise ReaderError("le fichier CSV est vide")
    return ReadResult("\n".join(lines), "csv", [f"encodage détecté : {enc}"] if enc.lower() not in ("utf-8", "utf-8-sig") else [])


def read_xlsx(path: Path) -> ReadResult:
    from openpyxl import load_workbook
    try:
        wb = load_workbook(path, read_only=True, data_only=True)
    except Exception as e:
        raise ReaderError(f"classeur Excel illisible ({type(e).__name__})") from e
    blocks = []
    for ws in wb.worksheets:
        lines = _rows_to_lines(row for _, row in zip(range(MAX_ROWS_PER_SHEET), ws.iter_rows(values_only=True)))
        if lines:
            blocks.append(f"--- FEUILLE « {ws.title} » ---\n" + "\n".join(lines))
    wb.close()
    if not blocks:
        raise ReaderError("le classeur Excel est vide")
    return ReadResult("\n\n".join(blocks), "xlsx")


def _ocr_pdf(path: Path) -> str:
    try:
        import pdfplumber
        import pytesseract
    except ImportError as e:
        raise ReaderError("PDF numérisé : l'OCR (pytesseract + Tesseract) n'est pas installé") from e
    out = []
    try:
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages[:MAX_PDF_PAGES]:
                out.append(pytesseract.image_to_string(page.to_image(resolution=250).original, lang="fra+eng"))
    except Exception as e:
        raise ReaderError(f"OCR impossible ({type(e).__name__}) : Tesseract est-il installé ?") from e
    return "\n".join(out)


def read_pdf(path: Path) -> ReadResult:
    import pdfplumber
    warnings: list[str] = []
    try:
        with pdfplumber.open(path) as pdf:
            n = len(pdf.pages)
            parts = []
            for page in pdf.pages[:MAX_PDF_PAGES]:
                tables = page.extract_tables() or []
                txt = page.extract_text() or ""
                if tables:
                    txt += "\n" + "\n".join(" | ".join(_cell(c) for c in row) for t in tables for row in t if any(row))
                parts.append(txt)
    except Exception as e:
        raise ReaderError(f"PDF illisible ({type(e).__name__})") from e
    text = "\n".join(parts).strip()
    if n and len(text) / min(n, MAX_PDF_PAGES) < OCR_MIN_CHARS_PER_PAGE:
        text = _ocr_pdf(path).strip()
        warnings.append("PDF numérisé : lecture par OCR")
    if len(text) < 30:
        raise ReaderError("aucun texte lisible dans le PDF")
    if n > MAX_PDF_PAGES:
        warnings.append(f"PDF tronqué à {MAX_PDF_PAGES} pages sur {n}")
    return ReadResult(text, "pdf", warnings)


def read_file(path: Path) -> ReadResult:
    ext = path.suffix.lower()
    with path.open("rb") as f:
        kind = sniff_kind(f.read(4096), ext)
    return {"csv": read_csv, "xlsx": read_xlsx, "pdf": read_pdf}[kind](path)


def fit_budget(text: str, budget: int) -> str:
    """Tronque à `budget` caractères en gardant d'abord l'en-tête puis les lignes de totaux
    (numéros de compte courts : classes et sous-classes SYSCOHADA), puis le détail dans l'ordre."""
    if len(text) <= budget:
        return text
    lines = text.splitlines()
    head, rest = lines[:5], lines[5:]
    summary = [l for l in rest if re.match(r"^\s*\d{1,3}\b", l)]
    summary_set = set(summary)
    detail = [l for l in rest if l not in summary_set]
    out, used = [], 0
    for l in head + summary + detail:
        if used + len(l) + 1 > budget - 40:
            break
        out.append(l)
        used += len(l) + 1
    return "\n".join(out) + "\n[… texte tronqué …]"
