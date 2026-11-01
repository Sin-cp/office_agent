import io
import unittest
import zipfile
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from lxml import etree
from app import document_service as service
import test_files


def marker(kind, identity='0', name='_GoBack'):
    node = OxmlElement('w:' + kind)
    node.set(qn('w:id'), identity)
    if kind == 'bookmarkStart':
        node.set(qn('w:name'), name)
    return node


def pair(p, identity='0'):
    p._p.append(marker('bookmarkStart', identity))
    p._p.append(marker('bookmarkEnd', identity))


def encoded(doc):
    output = io.BytesIO()
    doc.save(output)
    return output.getvalue()


def bookmark_positions(p):
    offset, result = 0, []
    runs = {r._r: r.text for r in p.runs}
    for child in p._p:
        if child.tag == qn('w:r'):
            offset += len(runs[child])
        elif child.tag in {qn('w:bookmarkStart'), qn('w:bookmarkEnd')}:
            result.append((etree.QName(child).localname, dict(child.attrib), offset))
    return result


class MarkerTests(unittest.TestCase):
    setUp = test_files.FilesTest.setUp
    tearDown = test_files.FilesTest.tearDown
    upload = test_files.FilesTest.upload

    def edit(self, doc, new_text, index=0, status=201):
        original = encoded(doc)
        file = self.upload(original).json()
        content = self.client.get(f"/api/files/{file['id']}/content").json()
        block = content['blocks'][index]
        response = self.client.post(f"/api/files/{file['id']}/edits", json=dict(
            revision=content['revision'], block_id=block['id'], expected_text=block['text'], new_text=new_text))
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(self.client.get(f"/api/files/{file['id']}/download").content, original)
        if status != 201:
            self.assertFalse(block['editable'])
            self.assertEqual(response.json()['detail'], block['reason'])
            return block['reason']
        self.assertTrue(block['editable'])
        result = self.client.get(f"/api/files/{response.json()['id']}/download").content
        with zipfile.ZipFile(io.BytesIO(original)) as before, zipfile.ZipFile(io.BytesIO(result)) as after:
            self.assertEqual(before.namelist(), after.namelist())
            for name in before.namelist():
                if name != 'word/document.xml':
                    self.assertEqual(before.read(name), after.read(name))
        return Document(io.BytesIO(result))

    def test_user_structure_preserves_tail_bookmark_and_line_break(self):
        doc = Document(); p = doc.add_paragraph('测试 office agent')
        p.add_run().add_break(); p.add_run('test'); pair(p)
        unchanged = doc.add_paragraph('其他段落')
        proof = OxmlElement('w:proofErr'); proof.set(qn('w:type'),'spellStart'); unchanged._p.append(proof)
        before_other = etree.tostring(unchanged._p)
        result = self.edit(doc, '测试 office agent\n测试内容')
        self.assertEqual(result.paragraphs[0].text, '测试 office agent\n测试内容')
        positions = bookmark_positions(result.paragraphs[0])
        self.assertEqual([x[2] for x in positions], [len(result.paragraphs[0].text)] * 2)
        self.assertEqual(positions[0][1][qn('w:name')], '_GoBack')
        self.assertEqual(etree.tostring(result.paragraphs[1]._p), before_other)

    def test_leading_and_trailing_multiple_bookmarks_empty_runs_and_format(self):
        doc = Document(); p = doc.add_paragraph()
        p.add_run('').bold = True
        pair(p, '1'); pair(p, '2')
        p.add_run('原文').italic = True
        pair(p, '3')
        p.add_run('')
        result = self.edit(doc, '修改后\n内容').paragraphs[0]
        self.assertEqual([x[2] for x in bookmark_positions(result)], [0,0,0,0,len(result.text),len(result.text)])
        self.assertTrue(result.runs[0].bold)
        self.assertEqual(result.text, '修改后\n内容')

    def test_empty_paragraph_inserts_before_bookmarks(self):
        for empty_runs in (False, True):
            with self.subTest(empty_runs=empty_runs):
                doc = Document(); p = doc.add_paragraph()
                if empty_runs:
                    p.add_run('')
                pair(p)
                if empty_runs:
                    p.add_run('')
                result = self.edit(doc, '新增文字').paragraphs[0]
                self.assertEqual([x[2] for x in bookmark_positions(result)], [4,4])

    def test_proofing_markers_removed_only_in_target(self):
        doc = Document(); p = doc.add_paragraph()
        for kind in ('spellStart', 'gramStart'):
            proof = OxmlElement('w:proofErr'); proof.set(qn('w:type'),kind); p._p.append(proof)
        p.add_run('原文')
        for kind in ('spellEnd', 'gramEnd'):
            proof = OxmlElement('w:proofErr'); proof.set(qn('w:type'),kind); p._p.append(proof)
        pair(p)
        result = self.edit(doc, '新文字').paragraphs[0]
        self.assertEqual(len(list(result._p.iter(qn('w:proofErr')))), 0)
        self.assertEqual(len(bookmark_positions(result)), 2)

    def test_middle_nonempty_and_malformed_bookmarks_rejected(self):
        for kind in ('middle','range','missing','reversed','duplicate','mismatch'):
            with self.subTest(kind=kind):
                doc = Document(); p = doc.add_paragraph('前')
                if kind == 'middle': pair(p); p.add_run('后')
                if kind == 'range':
                    p._p.append(marker('bookmarkStart')); p.add_run('范围'); p._p.append(marker('bookmarkEnd'))
                if kind == 'missing': p._p.append(marker('bookmarkStart'))
                if kind == 'reversed': p._p.append(marker('bookmarkEnd')); p._p.append(marker('bookmarkStart'))
                if kind == 'duplicate': pair(p); pair(p)
                if kind == 'mismatch': p._p.append(marker('bookmarkStart')); p._p.append(marker('bookmarkEnd','2'))
                self.assertIn('书签', self.edit(doc, '新', status=422))

    def test_cross_paragraph_bookmarks_reject_endpoints_and_interior(self):
        doc = Document()
        first = doc.add_paragraph('首'); first._p.append(marker('bookmarkStart'))
        doc.add_paragraph('中间')
        last = doc.add_paragraph('尾'); last._p.insert(0,marker('bookmarkEnd'))
        for index in range(3):
            self.assertIn('跨越段落', self.edit(doc, '新', index=index, status=422))

    def test_specific_reasons_for_complex_structures(self):
        for tag, reason, in_run in [('hyperlink','超链接',False),('drawing','图片',True),('fldChar','域',True),('ins','修订',False),('commentRangeStart','批注',False)]:
            with self.subTest(tag=tag):
                doc = Document(); p = doc.add_paragraph('test'); node = OxmlElement('w:'+tag)
                (p.runs[0]._r if in_run else p._p).append(node)
                self.assertIn(reason, self.edit(doc, '新', status=422))

    def test_writer_checks_structure_before_mutation(self):
        doc = Document(); p = doc.add_paragraph('旧')
        p._p.append(OxmlElement('w:hyperlink'))
        before = etree.tostring(p._p)
        with self.assertRaisesRegex(ValueError,'超链接'):
            service.write_replacement(encoded(doc), doc, p, '新', io.BytesIO())
        self.assertEqual(etree.tostring(p._p), before)
