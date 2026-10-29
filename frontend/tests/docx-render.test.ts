// @vitest-environment jsdom
import { expect, it } from 'vitest';
import JSZip from 'jszip';
import { renderAsync } from 'docx-preview';
it('renders a DOCX heading and table in document order', async () => {
  const zip = new JSZip();
  zip.file('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>');
  zip.file('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>');
  zip.file('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:rPr><w:b/></w:rPr><w:t>项目标题</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>表格内容</w:t></w:r></w:p></w:tc></w:tr></w:tbl><w:sectPr><w:pgSz w:w="11906" w:h="16838"/></w:sectPr></w:body></w:document>');
  const container = document.createElement('div');
  await renderAsync(await zip.generateAsync({ type: 'arraybuffer' }), container, undefined, { useBase64URL: true, renderAltChunks: false });
  expect(container.querySelector('section.docx')).not.toBeNull();
  expect(container.querySelector('table')?.textContent).toBe('表格内容');
  expect(container.textContent).toContain('项目标题');
});
