"""Local DOCX storage. Never trust a client filename as a filesystem path."""

import os
import zipfile
import zlib
from pathlib import Path
from xml.etree import ElementTree as ET

MAX_BYTES = 10 * 1024 * 1024
DOCX_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
)


def data_directory() -> Path:
    return Path(
        os.environ.get(
            "OFFICE_AGENT_DATA_DIR", Path(__file__).resolve().parents[1] / "data"
        )
    ).resolve()


def validate_docx(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if len(entries) > 2000 or len(names) != len(set(names)):
                raise ValueError("文档压缩包结构异常")
            if sum(e.file_size for e in entries) > 50 * 1024 * 1024:
                raise ValueError("文档解压后大小超过限制")
            if any(e.flag_bits & 1 for e in entries):
                raise ValueError("不支持加密文档")
            if any("vba" in name.lower() for name in names):
                raise ValueError("不支持含宏的文档")
            for required in ("[Content_Types].xml", "_rels/.rels", "word/document.xml"):
                if required not in names:
                    raise ValueError("文件不是有效的 DOCX 文档")
            if archive.testzip() is not None:
                raise ValueError("文档已损坏")

            def xml(name: str):
                raw = archive.read(name)
                if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
                    raise ValueError("不支持的 XML 结构")
                return ET.fromstring(raw)

            content = xml("[Content_Types].xml")
            if any(
                "macro" in e.attrib.get("ContentType", "").lower()
                or "vba" in e.attrib.get("ContentType", "").lower()
                for e in content
            ):
                raise ValueError("不支持含宏的文档")
            if not any(
                e.attrib.get("PartName") == "/word/document.xml"
                and e.attrib.get("ContentType") == DOCX_TYPE
                for e in content
            ):
                raise ValueError("文件内容类型不是 DOCX")
            relationships = xml("_rels/.rels")
            if not any(
                e.attrib.get("Type", "").endswith("/officeDocument")
                and e.attrib.get("Target", "").lstrip("/") == "word/document.xml"
                and e.attrib.get("TargetMode") != "External"
                for e in relationships
            ):
                raise ValueError("文档主文件关系无效")
            document = xml("word/document.xml")
            if document.tag not in (
                "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}document",
                "{http://purl.oclc.org/ooxml/wordprocessingml/main}document",
            ):
                raise ValueError("文档正文结构无效")
    except (
        zipfile.BadZipFile,
        zlib.error,
        ET.ParseError,
        KeyError,
        RuntimeError,
        NotImplementedError,
        EOFError,
    ) as exc:
        raise ValueError("文件不是有效的 DOCX，或文档已经损坏") from exc
