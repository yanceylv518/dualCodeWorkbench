import io
import zipfile

import pytest

from dualcode.document_text import InvalidDocumentError, extract_docx_text


def docx(document_xml: str) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("word/document.xml", document_xml)
    return output.getvalue()


def test_extract_docx_text_preserves_paragraphs_tabs_and_breaks() -> None:
    xml = """
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body>
        <w:p><w:r><w:t>第一段</w:t><w:tab/><w:t>字段</w:t></w:r></w:p>
        <w:p><w:r><w:t>第二段</w:t><w:br/><w:t>换行</w:t></w:r></w:p>
      </w:body>
    </w:document>
    """

    assert extract_docx_text(docx(xml)) == "第一段\t字段\n第二段\n换行"


def test_extract_docx_text_rejects_invalid_archives() -> None:
    with pytest.raises(InvalidDocumentError, match="Word 文档"):
        extract_docx_text(b"not-a-docx")
