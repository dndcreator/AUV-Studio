from __future__ import annotations

import io
import zipfile
import pytest

from app.evidence import parse_evidence_upload


@pytest.mark.parametrize("name,content", [("large.docx", lambda: _docx("large " * 200)), ("large.xlsx", lambda: _xlsx())])
def test_office_containers_have_their_own_expansion_limit(monkeypatch, name, content) -> None:
    monkeypatch.setattr("app.evidence.MAX_EXPANDED_BYTES", 64)
    result = parse_evidence_upload(name, content())
    assert result["items"] == []
    assert "expanded Office document" in result["errors"][0]["reason"]


def _zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _docx(text: str) -> bytes:
    xml = f'<w:document xmlns:w="urn:test"><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>'
    return _zip({"word/document.xml": xml.encode()})


def _xlsx() -> bytes:
    shared = '<sst xmlns="urn:test"><si><t>Segment</t></si><si><t>Students</t></si></sst>'
    sheet = '<worksheet xmlns="urn:test"><sheetData><row><c t="s"><v>0</v></c><c t="s"><v>1</v></c></row></sheetData></worksheet>'
    return _zip({"xl/sharedStrings.xml": shared.encode(), "xl/worksheets/sheet1.xml": sheet.encode()})


def test_parse_mixed_evidence_package_locally() -> None:
    package = _zip(
        {
            "notes.md": "Market background".encode(),
            "data.csv": "age,count\n18-22,120".encode(),
            "facts.json": b'{"source":"survey","value":42}',
            "report.docx": _docx("Interview finding"),
            "segments.xlsx": _xlsx(),
            "legacy.xls": b"unsupported",
        }
    )
    result = parse_evidence_upload("research.zip", package)

    assert result["summary"]["parsed_files"] == 5
    assert result["summary"]["skipped_files"] == 1
    content = "\n".join(item["content"] for item in result["items"])
    assert "18-22 | 120" in content
    assert "Interview finding" in content
    assert "Segment | Students" in content
    assert result["skipped"][0]["name"] == "legacy.xls"


def test_evidence_zip_rejects_unsafe_paths_without_extracting() -> None:
    result = parse_evidence_upload("unsafe.zip", _zip({"../secret.txt": b"no", "safe.txt": b"yes"}))
    assert [item["name"] for item in result["items"]] == ["safe.txt"]
    assert result["errors"][0]["reason"] == "unsafe archive path"
