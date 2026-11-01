"""Deterministic document tools, reusable by a future Agent.

Only direct body paragraphs are editable. Preserve all ZIP members except
word/document.xml; replace the selected paragraph's text, never global matches.
"""
from copy import deepcopy
import hashlib
import io
import zipfile
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from lxml import etree


def read_document(path: Path):
    data = path.read_bytes()
    document = Document(io.BytesIO(data))
    return data, document, hashlib.sha256(data).hexdigest()


def heading_level(paragraph):
    properties = paragraph._p.pPr
    outline = properties.find(qn('w:outlineLvl')) if properties is not None else None
    if outline is not None:
        value = int(outline.get(qn('w:val')))
        return value + 1 if value < 9 else None
    style = paragraph.style
    visited = set()
    while style is not None and style.style_id not in visited:
        visited.add(style.style_id)
        properties = style.element.find(qn('w:pPr'))
        outline = properties.find(qn('w:outlineLvl')) if properties is not None else None
        if outline is not None:
            value = int(outline.get(qn('w:val')))
            return value + 1 if value < 9 else None
        style = style.base_style
    return None


def bookmark_issues(document):
    """Find cross-paragraph ranges, including paragraphs inside their endpoints."""
    paragraphs = list(document.element.iter(qn('w:p')))
    positions = {node: index for index, node in enumerate(paragraphs)}
    markers = {}
    issues = {}
    for node in document.element.iter():
        if node.tag not in {qn('w:bookmarkStart'), qn('w:bookmarkEnd')}:
            continue
        parent = next((ancestor for ancestor in node.iterancestors() if ancestor.tag == qn('w:p')), None)
        identity = node.get(qn('w:id'))
        markers.setdefault(identity, []).append((node, parent))
    for identity, entries in markers.items():
        valid = (identity is not None and identity != '' and len(entries) == 2
                 and entries[0][0].tag == qn('w:bookmarkStart')
                 and entries[1][0].tag == qn('w:bookmarkEnd'))
        if not valid:
            for _, parent in entries:
                if parent is not None:
                    issues[parent] = '书签标记不完整、重复或顺序异常，暂不支持修改'
            continue
        first, last = entries[0][1], entries[1][1]
        if first != last:
            if first in positions and last in positions:
                for paragraph in paragraphs[positions[first]:positions[last] + 1]:
                    issues[paragraph] = '书签跨越段落，暂不支持整段替换'
            else:
                for parent in (first, last):
                    if parent is not None:
                        issues[parent] = '书签跨越段落，暂不支持整段替换'
    return issues


def editability(paragraph, issues=None):
    """Return one shared eligibility decision and a specific user-facing reason."""
    unsupported = {
        'hyperlink': '超链接', 'drawing': '图片或绘图', 'pict': '图片或绘图',
        'object': '嵌入对象', 'fldSimple': '域', 'fldChar': '域', 'instrText': '域',
        'ins': '修订', 'del': '修订', 'moveFrom': '修订', 'moveTo': '修订',
        'commentRangeStart': '批注范围', 'commentRangeEnd': '批注范围', 'commentReference': '批注',
    }
    # Include revisions in paragraph/run properties, not just direct children.
    for node in paragraph._p.iter():
        local = etree.QName(node).localname
        reason = unsupported.get(local)
        if reason or local in {'pPrChange', 'rPrChange'} or local.startswith(('moveFromRange', 'moveToRange')):
            return False, f"段落包含{reason or '修订'}，暂不支持修改"
    if issues is None:
        issues = bookmark_issues(paragraph.part.document)
    if paragraph._p in issues:
        return False, issues[paragraph._p]
    allowed = {qn('w:pPr'), qn('w:r'), qn('w:bookmarkStart'), qn('w:bookmarkEnd'), qn('w:proofErr')}
    if any(child.tag not in allowed for child in paragraph._p):
        return False, '段落包含尚不支持的内容结构，暂不支持修改'
    for run in paragraph.runs:
        for child in run._r:
            if child.tag not in {qn('w:rPr'), qn('w:t'), qn('w:tab'), qn('w:br'), qn('w:cr')}:
                return False, '文字片段包含尚不支持的内容结构，暂不支持修改'
            if child.tag == qn('w:br') and child.get(qn('w:type'), 'textWrapping') != 'textWrapping':
                return False, '段落包含分页符或分栏符，暂不支持修改'
    offset = 0
    starts, ends = {}, {}
    runs = {run._r: run.text for run in paragraph.runs}
    for child in paragraph._p:
        if child.tag == qn('w:r'):
            offset += len(runs[child])
        elif child.tag in {qn('w:bookmarkStart'), qn('w:bookmarkEnd')}:
            identity = child.get(qn('w:id'))
            target = starts if child.tag == qn('w:bookmarkStart') else ends
            if not identity or identity in target or (target is ends and identity not in starts):
                return False, '书签标记不完整、重复或顺序异常，暂不支持修改'
            target[identity] = offset
    if starts.keys() != ends.keys():
        return False, '书签标记不完整或跨越段落，暂不支持修改'
    if any(starts[key] != ends[key] or starts[key] not in (0, offset) for key in starts):
        return False, '书签涉及正文范围或位于正文中间，暂不支持整段替换'
    return True, ''


def editable(paragraph):
    # Compatibility helper; all callers share the same detailed decision.
    return editability(paragraph)[0]


def blocks(document, file_id):
    result = []
    issues = bookmark_issues(document)
    for index, paragraph in enumerate(document.paragraphs):
        level = heading_level(paragraph)
        can_edit, reason = editability(paragraph, issues)
        result.append(dict(id=f'{file_id}:p:{index}', position=index + 1,
                           kind='heading' if level else 'paragraph', level=level,
                           text=paragraph.text, style=paragraph.style.name if paragraph.style else '',
                           editable=can_edit,
                           reason=reason))
    return result


def write_replacement(data, document, paragraph, text, destination):
    can_edit, reason = editability(paragraph, bookmark_issues(document))
    if not can_edit:
        raise ValueError(reason)
    old_runs = list(paragraph.runs)
    # Insert where the first actual content was, so leading empty runs cannot
    # pull new text ahead of a leading bookmark. Empty paragraphs insert before
    # all bookmarks, keeping those bookmarks at the new paragraph's end.
    content_run = next((run._r for run in old_runs if run.text), None)
    bookmark = next((node for node in paragraph._p if node.tag == qn('w:bookmarkStart')), None)
    replacement = paragraph.add_run(text)
    if old_runs and old_runs[0]._r.rPr is not None:
        replacement._r.insert(0, deepcopy(old_runs[0]._r.rPr))
    anchor = content_run if content_run is not None else bookmark
    if anchor is not None:
        anchor.addprevious(replacement._r)
    for run in old_runs:
        paragraph._p.remove(run._r)
    for marker in list(paragraph._p.iter(qn('w:proofErr'))):
        marker.getparent().remove(marker)
    xml = etree.tostring(document.element, encoding='UTF-8', xml_declaration=True, standalone=True)
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(destination, 'w') as output:
        for info in source.infolist():
            output.writestr(info, xml if info.filename == 'word/document.xml' else source.read(info.filename))
