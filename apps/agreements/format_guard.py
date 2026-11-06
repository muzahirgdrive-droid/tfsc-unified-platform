"""Typed or pasted service wording, printed in the agreement's own formatting.

Custom and company-specific service text arrives however it was written: from Word, an email or a PDF. structure() turns it
into document blocks without rewording anything:

* a line that starts with a bullet symbol becomes a bullet; "1." / "1)" / "(1)" a numbered item, "(a)" / "a)" / "a." a
  lettered item (each list restarts at its own first number);
* a run of lines that each begin with a short capitalised label and a colon ("System Access: Provide ...") becomes bullets,
  even without a symbol, and the label of any bullet or numbered item prints in bold;
* a line ending in ":" right before a list is its lead-in, printed left-aligned and kept with the list;
* lines broken in the middle of a sentence (a paste from a PDF or an email) are joined again;
* tabs, non-breaking and repeated spaces are cleaned and the first letter of each block is capitalised.

format_guard.js is a line-for-line port used by the builder's live "How it will print" preview; tests keep the two in step
on a shared set of examples (tests/format_examples.json).
"""
from __future__ import annotations

import re

BULLET_SYMBOLS = '•●◦▪▫■□➢➤►▶▸✓✔∙·○◆❖'
DASH_SYMBOLS = '-–—*'
# Spaces of every width become one plain space; invisible characters are removed.
SPACES = '\t               　'
INVISIBLE = '​‌‍⁠﻿­'
BREAKS = '  \u000b\u000c\u0085'

_SYMBOL = '[' + re.escape(BULLET_SYMBOLS) + ']'
_DASH = '[' + re.escape(DASH_SYMBOLS) + ']'
_BULLET = re.compile('^(?:' + _SYMBOL + ' *|' + _DASH + ' +)(?=[^ ])(.*)$')
_MARKER = re.compile('^(?:' + _SYMBOL + '|' + _DASH + ')$')
_DECIMAL = re.compile(r'^(?:\(([0-9]{1,2})\)|([0-9]{1,2})[.)]) +(?=[^ ])(.*)$')
_LETTER = re.compile(r'^(?:\(([a-z])\)|([a-z])[.)]) +(?=[^ ])(.*)$')
_LABEL = re.compile(r'^([^:]{1,80}?) *: +(?=[^ ])(.*)$')
_CLEAN_RUNS = re.compile('[' + SPACES + ']+| {2,}')
_TERMINAL = re.compile(r'[.:;!?]["\'”’)\]]*$')
MAX_LABEL_WORDS = 8
WRAPPED_LINE = 60      # a line this long that stops mid-sentence was wrapped by the program it was copied from


def _is_upper(ch: str) -> bool:
    return ch.lower() != ch and ch.upper() == ch


def _is_lower(ch: str) -> bool:
    return ch.upper() != ch and ch.lower() == ch


def label_of(text: str) -> str:
    """The short label at the start of an item ("System Access" in "System Access: Provide ..."), or ''."""
    m = _LABEL.match(text)
    if not m:
        return ''
    label = m.group(1).strip()
    words = label.split(' ')
    if not label or len(words) > MAX_LABEL_WORDS or any(ch in label for ch in '.;!?"“”'):
        return ''
    if not (_is_upper(label[0]) or '0' <= label[0] <= '9'):
        return ''
    return label


def _normalise(text: str) -> str:
    text = str(text or '').replace('\r\n', '\n').replace('\r', '\n')
    for ch in BREAKS:
        text = text.replace(ch, '\n')
    for ch in INVISIBLE:
        text = text.replace(ch, '')
    for ch in SPACES:
        text = text.replace(ch, ' ')
    return text


def _tidy(text: str, stats: dict | None = None) -> str:
    text = re.sub(' {2,}', ' ', text).strip(' ')
    first = text.split(' ', 1)[0]
    if text and _is_lower(text[0]) and first == first.lower():
        text = text[0].upper() + text[1:]
        if stats is not None:
            stats['capitalised'] += 1
    return text


def _continues(previous: str, previous_line: int, line: str) -> bool:
    """True when `line` carries on the sentence of `previous` (a wrapped or broken line); previous_line is the length of the
    last line of `previous` as typed."""
    if _TERMINAL.search(previous):
        return False
    return _is_lower(line[0]) or line[0] in '(&,' or previous_line >= WRAPPED_LINE


def _join(previous: str, line: str) -> str:
    if previous.endswith('-') and len(previous) > 1 and previous[-2] != ' ' and _is_lower(line[0]):
        return previous + line          # "decision-" + "maker"
    return previous + ' ' + line


def _classify(line: str) -> tuple:
    """(kind, text, extra) for one stripped, non-empty line."""
    if _MARKER.match(line):
        return 'marker', '', None
    m = _BULLET.match(line)
    if m:
        return 'bullet', m.group(1), None
    m = _DECIMAL.match(line)
    if m:
        return 'number', m.group(3), ('decimal', int(m.group(1) or m.group(2)))
    m = _LETTER.match(line)
    if m:
        return 'number', m.group(3), ('letter', ord(m.group(1) or m.group(2)) - 96)
    if label_of(line):
        return 'label', line, None
    return 'text', line, None


def inspect(text: str) -> tuple[list[dict], dict]:
    """The blocks for `text` and what was done to it (for the builder's preview chips)."""
    raw = str(text or '')
    lines = _normalise(raw).split('\n')
    stats = {'bullets': 0, 'numbers': 0, 'leads': 0, 'labels': 0, 'joined': 0, 'capitalised': 0,
             'spaces': sum(len(_CLEAN_RUNS.findall(line)) for line in raw.replace('\r\n', '\n').replace('\r', '\n').split('\n'))}
    lines = [line.strip(' ') for line in lines]
    entries = []                                 # (kind, text, extra) with None for a blank line
    pending = False                              # a bullet symbol alone on its line: the next line is the item
    for line in lines:
        if not line:
            entries.append(None)
            continue
        kind, body, extra = _classify(line)
        if pending:
            pending = False
            if kind in ('text', 'label'):
                kind = 'bullet'
        if kind == 'marker':
            pending = True
            continue
        entries.append((kind, body, extra))

    def neighbour(index: int, step: int):
        index += step
        while 0 <= index < len(entries) and entries[index] is None:
            index += step
        return entries[index] if 0 <= index < len(entries) else None

    def labelled(entry) -> bool:
        return bool(entry) and (entry[0] == 'label' or (entry[0] in ('bullet', 'number') and bool(label_of(_tidy(entry[1])))))

    # A run of "Label: text" lines is a list, and so is a labelled line beside a labelled bullet.
    for i, entry in enumerate(entries):
        if entry and entry[0] == 'label' and (labelled(neighbour(i, -1)) or labelled(neighbour(i, 1))):
            entries[i] = ('bullet', entry[1], 'run')

    blocks: list[dict] = []
    paragraph: list[str] = []
    last_blank = True
    last_line = 0                                # length of the last line added to the current paragraph or item

    def flush():
        if paragraph:
            blocks.append({'type': 'p', 'text': _tidy(paragraph[0], stats)})
            paragraph.clear()

    for entry in entries:
        if entry is None:
            flush()
            last_blank = True
            continue
        kind, body, extra = entry
        if kind in ('bullet', 'number'):
            flush()
            item = {'type': kind, 'text': _tidy(body, stats)}
            if kind == 'number':
                style, value = extra
                last = blocks[-1] if blocks else None
                if value == 1 or not (last and last['type'] == 'number' and last['style'] == style):
                    item['start'] = value                  # a new list (each list restarts at its own first number)
                item['style'] = style
            label = label_of(item['text'])
            if label:
                item['label'] = label
            blocks.append(item)
        elif not paragraph and not last_blank and kind == 'text' and blocks and blocks[-1]['type'] in ('bullet', 'number') \
                and _continues(blocks[-1]['text'], last_line, body):
            blocks[-1]['text'] = _join(blocks[-1]['text'], body)       # a list item wrapped onto the next line
            stats['joined'] += 1
        elif paragraph and kind == 'text' and _continues(paragraph[0], last_line, body):
            paragraph[0] = _join(paragraph[0], body)                    # a sentence broken by a paste
            stats['joined'] += 1
        else:
            flush()
            paragraph.append(body)
        last_blank = False
        last_line = len(body)
    flush()
    for i, block in enumerate(blocks[:-1]):
        if block['type'] == 'p' and block['text'].endswith(':') and blocks[i + 1]['type'] in ('bullet', 'number'):
            block['type'] = 'lead'
    stats['bullets'] = sum(b['type'] == 'bullet' for b in blocks)
    stats['numbers'] = sum(b['type'] == 'number' for b in blocks)
    stats['leads'] = sum(b['type'] == 'lead' for b in blocks)
    stats['labels'] = sum('label' in b for b in blocks)
    return blocks, stats


def structure(text: str) -> list[dict]:
    """Document blocks for typed or pasted service text: p, lead, bullet and number (with style, start and label)."""
    return inspect(text)[0]
