from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from pathlib import PurePosixPath
from typing import Any
from xml.etree import ElementTree as ET


MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_ARCHIVE_FILES = 100
MAX_EXPANDED_BYTES = 80 * 1024 * 1024
MAX_TEXT_CHARS = 120_000
SUPPORTED = {".txt", ".md", ".csv", ".json", ".docx", ".xlsx"}


def parse_evidence_upload(filename: str, data: bytes) -> dict[str, Any]:
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("evidence upload exceeds 25 MB")
    name = PurePosixPath(filename.replace("\\", "/")).name or "evidence"
    items: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    if name.lower().endswith(".zip"):
        _parse_archive(data, items=items, skipped=skipped, errors=errors)
    else:
        _parse_one(name, data, items=items, skipped=skipped, errors=errors)
    return {
        "version": 1,
        "package_name": name,
        "items": items,
        "skipped": skipped,
        "errors": errors,
        "summary": {
            "parsed_files": len(items),
            "skipped_files": len(skipped),
            "failed_files": len(errors),
            "characters": sum(len(str(item.get("content", ""))) for item in items),
        },
    }


def _parse_archive(
    data: bytes,
    *,
    items: list[dict[str, Any]],
    skipped: list[dict[str, str]],
    errors: list[dict[str, str]],
) -> None:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError("invalid ZIP evidence package") from exc
    files = [entry for entry in archive.infolist() if not entry.is_dir() and not entry.filename.startswith("__MACOSX/")]
    if len(files) > MAX_ARCHIVE_FILES:
        raise ValueError("evidence ZIP contains more than 100 files")
    if sum(entry.file_size for entry in files) > MAX_EXPANDED_BYTES:
        raise ValueError("expanded evidence ZIP exceeds 80 MB")
    for entry in files:
        path = PurePosixPath(entry.filename.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts:
            errors.append({"name": entry.filename, "reason": "unsafe archive path"})
            continue
        try:
            _parse_one(entry.filename, archive.read(entry), items=items, skipped=skipped, errors=errors)
        except (RuntimeError, zipfile.BadZipFile, OSError, ValueError) as exc:
            errors.append({"name": entry.filename, "reason": str(exc)[:240]})


def _parse_one(
    name: str,
    data: bytes,
    *,
    items: list[dict[str, Any]],
    skipped: list[dict[str, str]],
    errors: list[dict[str, str]],
) -> None:
    suffix = PurePosixPath(name).suffix.lower()
    if suffix not in SUPPORTED:
        skipped.append({"name": name, "reason": "unsupported file type"})
        return
    try:
        if suffix in {".txt", ".md"}:
            content = _decode_text(data)
            kind = "document"
        elif suffix == ".csv":
            content = _parse_csv(data)
            kind = "table"
        elif suffix == ".json":
            content = json.dumps(json.loads(_decode_text(data)), ensure_ascii=False, indent=2)
            kind = "structured"
        elif suffix == ".docx":
            content = _parse_docx(data)
            kind = "document"
        else:
            content = _parse_xlsx(data)
            kind = "table"
    except Exception as exc:  # noqa: BLE001
        errors.append({"name": name, "reason": str(exc)[:240]})
        return
    content = content.strip()[:MAX_TEXT_CHARS]
    if not content:
        skipped.append({"name": name, "reason": "no readable content"})
        return
    items.append({"id": f"src_{len(items) + 1}", "name": name, "kind": kind, "content": content})


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "utf-16"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("unsupported text encoding")


def _parse_csv(data: bytes) -> str:
    rows = list(csv.reader(io.StringIO(_decode_text(data))))
    return "\n".join(" | ".join(cell.strip() for cell in row[:50]) for row in rows[:2000])


def _parse_docx(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        xml = archive.read("word/document.xml")
    root = ET.fromstring(xml)
    paragraphs: list[str] = []
    for paragraph in root.iter():
        if paragraph.tag.endswith("}p"):
            text = "".join(node.text or "" for node in paragraph.iter() if node.tag.endswith("}t")).strip()
            if text:
                paragraphs.append(text)
    return "\n".join(paragraphs)


def _parse_xlsx(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = set(archive.namelist())
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root:
                shared.append("".join(node.text or "" for node in item.iter() if node.tag.endswith("}t")))
        sheets = sorted(name for name in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name))
        output: list[str] = []
        for sheet_name in sheets[:20]:
            output.append(f"## {PurePosixPath(sheet_name).stem}")
            root = ET.fromstring(archive.read(sheet_name))
            for row in [node for node in root.iter() if node.tag.endswith("}row")][:2000]:
                values: list[str] = []
                for cell in [node for node in row if node.tag.endswith("}c")][:50]:
                    value_node = next((node for node in cell.iter() if node.tag.endswith("}v")), None)
                    value = value_node.text if value_node is not None and value_node.text is not None else ""
                    if cell.attrib.get("t") == "s" and value.isdigit() and int(value) < len(shared):
                        value = shared[int(value)]
                    values.append(value)
                output.append(" | ".join(values))
        return "\n".join(output)


def compact_evidence(pack: dict[str, Any], max_chars: int = 24_000) -> dict[str, Any]:
    items = pack.get("items", []) if isinstance(pack, dict) else []
    compact: list[dict[str, Any]] = []
    remaining = max_chars
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or remaining <= 0:
            continue
        content = str(item.get("content", ""))[:remaining]
        compact.append({"id": item.get("id"), "name": item.get("name"), "kind": item.get("kind"), "content": content})
        remaining -= len(content)
    return {"version": 1, "package_name": pack.get("package_name", ""), "items": compact, "summary": pack.get("summary", {})}
