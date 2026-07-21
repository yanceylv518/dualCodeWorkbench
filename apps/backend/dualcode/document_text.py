import zipfile
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree


DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MAX_DOCX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
MAX_DOCX_XML_BYTES = 20 * 1024 * 1024
WORD_NAMESPACE = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class InvalidDocumentError(ValueError):
    pass


def extract_docx_text(source: bytes | Path) -> str:
    """Extract readable text from a bounded, valid Office Open XML document."""
    archive_source = BytesIO(source) if isinstance(source, bytes) else source
    try:
        with zipfile.ZipFile(archive_source) as archive:
            members = archive.infolist()
            if sum(item.file_size for item in members) > MAX_DOCX_UNCOMPRESSED_BYTES:
                raise InvalidDocumentError("Word 文档解压后超过安全限制")
            names = {item.filename for item in members}
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise InvalidDocumentError("Word 文档结构无效")
            document = archive.getinfo("word/document.xml")
            if document.file_size > MAX_DOCX_XML_BYTES:
                raise InvalidDocumentError("Word 文档正文超过安全限制")
            root = ElementTree.fromstring(archive.read(document))
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError, OSError) as exc:
        raise InvalidDocumentError("Word 文档无效或已损坏") from exc

    paragraphs: list[str] = []
    for paragraph in root.iter(f"{WORD_NAMESPACE}p"):
        fragments: list[str] = []
        for node in paragraph.iter():
            if node.tag == f"{WORD_NAMESPACE}t" and node.text:
                fragments.append(node.text)
            elif node.tag == f"{WORD_NAMESPACE}tab":
                fragments.append("\t")
            elif node.tag in {f"{WORD_NAMESPACE}br", f"{WORD_NAMESPACE}cr"}:
                fragments.append("\n")
        text = "".join(fragments).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)
