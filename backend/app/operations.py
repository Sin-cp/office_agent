"""Version-scoped, validated OOXML operations. Never execute model code."""
from copy import deepcopy
import io
import zipfile
from typing import Literal

from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from lxml import etree
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import document_service as service


class Operation(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    op: Literal['replace', 'delete', 'insert_paragraph', 'insert_table', 'insert_row', 'delete_row', 'insert_column', 'delete_column']
    target: str = Field(min_length=1, max_length=200)
    expected_text: str
    text: str = Field(default='', max_length=20000)
    where: Literal['before', 'after'] = 'after'
    style: Literal['inherit', 'paragraph', 'heading', 'bullet', 'number'] = 'inherit'
    level: int = Field(default=1, ge=1, le=9)
    index: int = Field(default=0, ge=0)
    cells: list[list[str]] = Field(default_factory=list, max_length=200)

    @field_validator('text', 'expected_text')
    @classmethod
    def xml_text(cls, value):
        if any(not (c in '\t\n\r' or 32 <= ord(c) <= 0xD7FF or 0xE000 <= ord(c) <= 0xFFFD or 0x10000 <= ord(c) <= 0x10FFFF) for c in value):
            raise ValueError('文字包含不支持的控制字符')
        return value.replace('\r\n', '\n').replace('\r', '\n')

    @field_validator('cells')
    @classmethod
    def valid_cells(cls, rows):
        if any(len(row) > 50 for row in rows) or sum(len(s) for row in rows for s in row) > 100000:
            raise ValueError('表格超过单次操作限制')
        return [[cls.xml_text(s) for s in row] for row in rows]


def table_reason(table):
    nodes = table._tbl
    if any(etree.QName(n).localname in {'ins', 'del', 'moveFrom', 'moveTo', 'tblPrChange', 'trPrChange', 'tcPrChange', 'tblGridChange'} for n in nodes.iter()):
        return '表格包含修订，暂不支持修改'
    if nodes.xpath('.//w:gridSpan | .//w:vMerge | .//w:hMerge | .//w:gridBefore | .//w:gridAfter'):
        return '合并单元格或不规则表格暂不支持修改'
    if nodes.xpath('.//w:tc//w:tbl'):
        return '嵌套表格暂不支持修改'
    if any(len(row.cells) != len(table.columns) for row in table.rows):
        return '不规则表格暂不支持修改'
    if nodes.xpath('.//w:bookmarkStart | .//w:bookmarkEnd | .//w:sectPr'):
        return '表格包含书签或分节信息，暂不支持修改'
    for row in table.rows:
        for cell in row.cells:
            if any(child.tag not in {qn('w:tcPr'), qn('w:p')} for child in cell._tc):
                return '单元格包含不支持的结构'
            for paragraph in cell.paragraphs:
                ok, reason = service.editability(paragraph)
                if not ok:
                    return reason
    return ''


def paragraph_text(p):
    # Include visible hyperlink text; retain complex nodes as read-only.
    return ''.join('\t' if n.tag == qn('w:tab') else '\n' if n.tag in {qn('w:br'), qn('w:cr')} else n.text or ''
                   for n in p._p.iter() if n.tag in {qn('w:t'), qn('w:tab'), qn('w:br'), qn('w:cr')})


def inventory(document, file_id):
    """Ordered read model plus references scoped to this exact loaded version."""
    blocks, refs = [], {}
    issues = service.bookmark_issues(document)
    pindex = 0

    def paragraph(p, identity, location, parent_reason=''):
        ok, reason = service.editability(p, issues)
        if p._p.xpath('./w:pPr/w:sectPr'):
            ok, reason = False, '段落包含分节信息，暂不支持修改'
        level = service.heading_level(p)
        numbered = bool(p._p.xpath('./w:pPr/w:numPr')) or bool(p.style and 'List' in p.style.name)
        block = dict(id=identity, kind='heading' if level else 'list' if numbered else 'paragraph',
                     text=paragraph_text(p), level=level, location=location,
                     editable=ok and not parent_reason, reason=parent_reason or reason)
        blocks.append(block)
        refs[identity] = (block, p)

    def table(t, identity, location, inherited=''):
        reason = inherited or table_reason(t)
        text = '\n'.join('\t'.join(''.join(n.text or '' for n in c._tc.iter(qn('w:t'))) for c in r.cells) for r in t.rows)
        block = dict(id=identity, kind='table', text=text, location=location, editable=not reason, reason=reason,
                     rows=len(t.rows), columns=len(t.columns), level=None)
        blocks.append(block)
        refs[identity] = (block, t)
        seen = set()
        for ri, row in enumerate(t.rows):
            for ci, cell in enumerate(row.cells):
                if cell._tc in seen:
                    continue
                seen.add(cell._tc)
                for pi, p in enumerate(cell.paragraphs):
                    paragraph(p, f'{identity}:r:{ri}:c:{ci}:p:{pi}', f'{location}第{ri+1}行第{ci+1}列第{pi+1}段', reason)
                for ti, nested in enumerate(cell.tables):
                    table(nested, f'{identity}:r:{ri}:c:{ci}:t:{ti}', f'{location}第{ri+1}行第{ci+1}列嵌套表格{ti+1}', '嵌套表格暂不支持修改')

    tindex = 0
    for child in document.element.body:
        if child.tag == qn('w:p'):
            paragraph(Paragraph(child, document), f'{file_id}:p:{pindex}', f'正文第{pindex+1}段')
            pindex += 1
        elif child.tag == qn('w:tbl'):
            table(Table(child, document), f'{file_id}:t:{tindex}', f'表格{tindex+1}')
            tindex += 1
    if not blocks:
        block = dict(id=f'{file_id}:end', kind='document_end', text='', location='空文档末尾', editable=True, reason='', level=None)
        blocks.append(block)
        refs[block['id']] = (block, document)
    return blocks, refs


def coverage(document):
    warnings = ['读取范围为正文和表格；页眉页脚、图片内容、文本框、批注和修订不作为完整阅读内容。']
    if any(n.tag not in {qn('w:p'), qn('w:tbl'), qn('w:sectPr')} for n in document.element.body):
        warnings.append('正文包含未解析的结构，以下回答不能视为覆盖全部内容。')
    return warnings


def replace_text(p, text):
    # Preserve unchanged runs and their formatting; changed text inherits the first
    # affected run. SequenceMatcher lets unaffected bold/italic spans survive.
    from difflib import SequenceMatcher
    runs = list(p.runs)
    old = ''.join(r.text for r in runs)
    if not runs:
        p.add_run(text)
        return
    spans, offset = [], 0
    for run in runs:
        spans.append((offset, offset + len(run.text), run))
        offset += len(run.text)
    pieces = []
    for tag, a, b, c, d in SequenceMatcher(None, old, text, autojunk=False).get_opcodes():
        if tag == 'equal':
            for start, end, run in spans:
                if max(start, a) < min(end, b):
                    pieces.append((old[max(start,a):min(end,b)], run))
        elif tag in ('insert', 'replace'):
            run = next((r for start, end, r in spans if start <= a < end), runs[-1])
            pieces.append((text[c:d], run))
    anchor = next((r._r for r in runs if r.text), runs[0]._r)
    for value, source in pieces:
        new = p.add_run(value)
        if source._r.rPr is not None:
            new._r.insert(0, deepcopy(source._r.rPr))
        anchor.addprevious(new._r)
    for run in runs:
        p._p.remove(run._r)
    for marker in list(p._p.iter(qn('w:proofErr'))):
        marker.getparent().remove(marker)


def apply(document, file_id, operations):
    if not operations or len(operations) > 500:
        raise ValueError('操作数量应为 1–500')
    original_xml = etree.tostring(document.element)
    _, refs = inventory(document, file_id)
    resolved, used = [], []
    for op in operations:
        if op.target not in refs:
            raise ValueError('目标不属于当前文档版本')
        block, obj = refs[op.target]
        if block['text'] != op.expected_text:
            raise ValueError('目标原文不匹配，请重新读取文档')
        if not block['editable']:
            raise ValueError(block['reason'])
        node = document.element.body if obj is document else obj._p if isinstance(obj, Paragraph) else obj._tbl
        # Reject overlapping edits before touching the in-memory document.
        for previous, previous_op in used:
            if node is previous and op.op.startswith('insert_') and previous_op.op.startswith('insert_') and op.op in ('insert_paragraph','insert_table') and previous_op.op in ('insert_paragraph','insert_table'):
                continue
            if node is previous or previous in node.iterancestors() or node in previous.iterancestors():
                raise ValueError('操作目标重叠，请将同一目标的修改合并')
        used.append((node, op))
        if obj is document and op.op not in ('insert_paragraph','insert_table'):
            raise ValueError('空文档只支持新增内容')
        if op.op == 'replace' and not isinstance(obj, Paragraph):
            raise ValueError('此操作需要段落目标')
        if op.op in ('insert_row', 'delete_row', 'insert_column', 'delete_column') and not isinstance(obj, Table):
            raise ValueError('此操作需要表格目标')
        if op.op == 'delete' and isinstance(obj, Paragraph):
            if node.getparent().tag == qn('w:tc'):
                raise ValueError('单元格段落请使用替换操作，可替换为空文字')
            if node.xpath('.//w:bookmarkStart | .//w:bookmarkEnd'):
                raise ValueError('含书签的段落不能删除')
        if op.op in ('insert_paragraph', 'insert_table') and obj is not document and node.getparent() is not document.element.body:
            raise ValueError('新增段落或表格需以正文块为锚点')
        if op.op == 'insert_table' and (not op.cells or not op.cells[0] or any(len(r) != len(op.cells[0]) for r in op.cells)):
            raise ValueError('新表格必须是非空矩形')
        if op.op in ('insert_row', 'delete_row', 'insert_column', 'delete_column'):
            count = len(obj.rows) if op.op.endswith('row') else len(obj.columns)
            invalid = op.index > count if op.op.startswith('insert') else op.index >= count or count <= 1
            if invalid:
                raise ValueError('行列位置无效或不能删除最后一行/列')
            if op.op == 'insert_row' and (len(op.cells) != 1 or len(op.cells[0]) != len(obj.columns)):
                raise ValueError('新增行的单元格数量不匹配')
            if op.op == 'insert_column' and (len(op.cells) != len(obj.rows) or any(len(r) != 1 for r in op.cells)):
                raise ValueError('新增列的单元格数量不匹配')
        resolved.append((op, block, obj, node))
    changes, insertion_tails = [], {}

    def insert(node, new, op):
        if node is document.element.body:
            return  # add_paragraph/add_table already placed before sectPr
        if op.where == 'before':
            node.addprevious(new)
        else:
            insertion_tails.get(node, node).addnext(new)
            insertion_tails[node] = new

    for op, block, obj, node in resolved:
        before = block['text']
        after = op.text
        if op.op == 'replace':
            replace_text(obj, op.text)
            set_style(document, obj, op)
        elif op.op == 'delete':
            node.getparent().remove(node)
            after = ''
        elif op.op == 'insert_paragraph':
            p = document.add_paragraph()
            if isinstance(obj, Paragraph) and obj._p.pPr is not None:
                p._p.insert(0, deepcopy(obj._p.pPr))
            p.add_run(op.text)
            if isinstance(obj, Paragraph) and obj.runs and obj.runs[0]._r.rPr is not None:
                p.runs[0]._r.insert(0, deepcopy(obj.runs[0]._r.rPr))
            set_style(document, p, op)
            insert(node, p._p, op)
            before = ''
        elif op.op == 'insert_table':
            t = document.add_table(rows=len(op.cells), cols=len(op.cells[0]))
            if 'Table Grid' in document.styles:
                t.style = 'Table Grid'
            for r, values in zip(t.rows, op.cells):
                for cell, value in zip(r.cells, values):
                    cell.text = value
            insert(node, t._tbl, op)
            before, after = '', '\n'.join('\t'.join(r) for r in op.cells)
        elif op.op == 'insert_row':
            template = obj.rows[min(op.index, len(obj.rows)-1)]._tr
            new = deepcopy(template)
            for tc, value in zip(new.findall(qn('w:tc')), op.cells[0]):
                reset_cell(tc, value)
            if op.index == len(obj.rows):
                obj._tbl.append(new)
            else:
                obj.rows[op.index]._tr.addprevious(new)
        elif op.op == 'delete_row':
            obj._tbl.remove(obj.rows[op.index]._tr)
        elif op.op == 'insert_column':
            grid = obj._tbl.tblGrid
            template_index = min(op.index, len(obj.columns)-1)
            col = deepcopy(grid.gridCol_lst[template_index])
            cells = [(r.cells[template_index]._tc, values[0]) for r, values in zip(obj.rows, op.cells)]
            for tc, value in cells:
                new = deepcopy(tc)
                reset_cell(new, value)
                (tc.addnext if op.index == len(obj.columns) else tc.addprevious)(new)
            if op.index == len(obj.columns):
                grid.append(col)
            else:
                grid.gridCol_lst[op.index].addprevious(col)
        elif op.op == 'delete_column':
            cells = [r.cells[op.index]._tc for r in obj.rows]
            for tc in cells:
                tc.getparent().remove(tc)
            obj._tbl.tblGrid.remove(obj._tbl.tblGrid.gridCol_lst[op.index])
        if op.op.endswith(('row', 'column')):
            after = '\n'.join('\t'.join(c.text for c in r.cells) for r in obj.rows)
        changes.append(dict(op=op.op, block_id=op.target, location=block['location'], before=before, after=after))
    if etree.tostring(document.element) == original_xml or all(c['before'] == c['after'] for c in changes) and all(op.style == 'inherit' for op in operations):
        raise ValueError('文字没有变化，无需生成新文件')
    return changes


def reset_cell(tc, text):
    p = tc.find(qn('w:p'))
    run_properties = p.find('.//' + qn('w:rPr')) if p is not None else None
    run_properties = deepcopy(run_properties) if run_properties is not None else None
    properties = deepcopy(p.find(qn('w:pPr'))) if p is not None and p.find(qn('w:pPr')) is not None else None
    for child in list(tc):
        if child.tag != qn('w:tcPr'):
            tc.remove(child)
    p = OxmlElement('w:p')
    if properties is not None:
        p.append(properties)
    tc.append(p)
    run = Paragraph(p, None).add_run(text)
    if run_properties is not None:
        run._r.insert(0, run_properties)


def set_style(document, p, op):
    if op.style == 'inherit':
        return
    name = {'paragraph': 'Normal', 'heading': f'Heading {op.level}', 'bullet': 'List Bullet', 'number': 'List Number'}[op.style]
    if name not in document.styles:
        raise ValueError(f'文档缺少样式 {name}，无法安全应用')
    p.style = name
    # Explicit style changes must not retain conflicting direct numbering.
    for child in p._p.xpath('./w:pPr/w:numPr | ./w:pPr/w:outlineLvl'):
        child.getparent().remove(child)


def write(data, document, destination):
    xml = etree.tostring(document.element, encoding='UTF-8', xml_declaration=True, standalone=True)
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(destination, 'w') as output:
        for info in source.infolist():
            output.writestr(info, xml if info.filename == 'word/document.xml' else source.read(info.filename))
