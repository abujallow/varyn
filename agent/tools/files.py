from __future__ import annotations

import re
from pathlib import Path

from fastapi import UploadFile

from config import DATA_DIR
from safety import detect_instructional_content
from varyn_settings import setting


UPLOAD_DIR = DATA_DIR / "uploads"
MAX_CONTEXT_CHARS = 18000
UPLOAD_CHUNK_BYTES = 64 * 1024

TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".html",
    ".css",
    ".scss",
    ".sql",
    ".xml",
    ".yaml",
    ".yml",
    ".toml",
}
ALLOWED_EXTENSIONS = TEXT_EXTENSIONS | {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp"}


class UploadValidationError(ValueError):
    pass


def safe_session_id(session_id: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", session_id or "local-preview")[:80]


def safe_filename(filename: str) -> str:
    name = Path(filename or "uploaded-file").name
    return re.sub(r"[^a-zA-Z0-9_. ()-]+", "-", name)[:160] or "uploaded-file"


def process_upload(upload: UploadFile, session_id: str) -> dict:
    filename = safe_filename(upload.filename or "uploaded-file")
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise UploadValidationError(f"File type {extension or 'unknown'} is not allowed.")

    max_bytes = int(setting("security.max_upload_bytes", 10 * 1024 * 1024))
    session_dir = UPLOAD_DIR / safe_session_id(session_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    path = session_dir / filename
    temporary = path.with_suffix(f"{path.suffix}.part")
    size = 0
    try:
        with temporary.open("wb") as target:
            while chunk := upload.file.read(UPLOAD_CHUNK_BYTES):
                size += len(chunk)
                if size > max_bytes:
                    raise UploadValidationError(
                        f"File exceeds the {max_bytes // (1024 * 1024)} MB upload limit."
                    )
                target.write(chunk)
        temporary.replace(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    extraction = extract_text(path, extension)
    text = extraction["text"][:MAX_CONTEXT_CHARS]
    extracted_chars = len(text)
    ready = bool(text.strip())
    instruction_flags = detect_instructional_content(text) if ready else []

    return {
        "name": filename,
        "path": str(path),
        "size": size,
        "extension": extension,
        "ready": ready,
        "status": "ready" if ready else "no_text",
        "extraction_status": extraction["status"],
        "message": extraction["message"],
        "extracted_chars": extracted_chars,
        "text_preview": text[:1200],
        "text": text,
        "instruction_flags": instruction_flags,
        "security_status": "flagged" if instruction_flags else "clear",
    }


def extract_text(path: Path, extension: str) -> dict:
    if extension in TEXT_EXTENSIONS:
        return extract_plain_text(path)

    if extension == ".pdf":
        return extract_pdf_text(path)

    if extension in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        return {
            "status": "unsupported",
            "message": "Image files can be loaded, but visual analysis is not implemented yet.",
            "text": "",
        }

    return {
        "status": "unsupported",
        "message": f"File type {extension or 'unknown'} is not supported for text extraction yet.",
        "text": "",
    }


def extract_plain_text(path: Path) -> dict:
    for encoding in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            text = path.read_text(encoding=encoding, errors="replace")
            return {
                "status": "extracted",
                "message": "Text extracted successfully.",
                "text": normalize_text(text),
            }
        except UnicodeDecodeError:
            continue

    return {
        "status": "failed",
        "message": "The file could not be decoded as text.",
        "text": "",
    }


def extract_pdf_text(path: Path) -> dict:
    try:
        from pypdf import PdfReader
    except ImportError:
        return {
            "status": "missing_dependency",
            "message": "PDF extraction requires the pypdf package. Run the agent start script to install updated requirements.",
            "text": "",
        }

    try:
        reader = PdfReader(str(path))
        pages = []
        for index, page in enumerate(reader.pages[:40], start=1):
            page_text = page.extract_text() or ""
            if page_text.strip():
                pages.append(f"[Page {index}]\n{page_text}")

        text = normalize_text("\n\n".join(pages))
        if not text:
            return {
                "status": "no_text",
                "message": "PDF uploaded, but no extractable text was found.",
                "text": "",
            }

        return {
            "status": "extracted",
            "message": f"PDF text extracted from {len(pages)} page(s).",
            "text": text,
        }
    except Exception as exc:
        return {
            "status": "failed",
            "message": f"PDF extraction failed: {exc}",
            "text": "",
        }


def normalize_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
