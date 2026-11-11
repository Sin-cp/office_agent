"""Deterministic DOCX naming shared by creation, editing and renaming.

Never trust a model-provided name as a path; it is only metadata here while the
filesystem key stays a UUID. Version suffixes are numbered, so repeated edits of
one file family become ``_修改版1``, ``_修改版2`` instead of stacking
``_修改版_修改版``.
"""
import re

from pydantic import BaseModel, ConfigDict, Field

INVALID = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
VERSION = re.compile(r'_修改版(\d+)?$')
MAX_STEM = 200


class Rename(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    file_id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=100)


def safe_name(raw, fallback='新文档'):
    text = INVALID.sub('_', (raw or '').strip())
    text = re.sub(r'\s+', ' ', text).strip(' .')
    if text.lower().endswith('.docx'):
        text = text[:-5]
    # Leave room for the extension so truncation never produces an unusable name.
    text = text[:215].strip(' .')
    if not text:
        text = fallback
    return text + '.docx'


def base_title(name):
    """Strip every trailing version suffix, legacy stacked ones included."""
    stem = name[:-5] if name.lower().endswith('.docx') else name
    while True:
        match = VERSION.search(stem)
        if not match:
            break
        stem = stem[:match.start()]
    return stem.strip(' .') or '文档'


def versioned_name(name, siblings):
    """Next ``<base>_修改版N.docx`` given the names already used in the family."""
    base = base_title(name)[:MAX_STEM]
    used = set()
    for sibling in siblings:
        title = sibling[:-5] if sibling.lower().endswith('.docx') else sibling
        if not title.startswith(base):
            continue
        match = re.fullmatch(r'_修改版(\d+)?', title[len(base):])
        if match:
            # A legacy ``_修改版`` without a number counts as version 0.
            used.add(int(match.group(1) or 0))
    return f'{base}_修改版{max(used, default=0) + 1}.docx'
