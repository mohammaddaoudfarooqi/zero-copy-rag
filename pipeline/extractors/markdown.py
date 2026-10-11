"""Markdown extractor: segment by headings, then character-window each section.

Emits character ranges rather than strings so the base class can convert them to
byte spans. The heading is carried as a byte range, never as text.

Each chunk also carries ``heading_path``: the byte spans of the headings it sits
under, outermost first, ending with its own section heading. Ancestors come from
heading levels. Documents exported flat often mark a group with an empty-bodied
heading at the same level as the sections in it ("### 1. Phase 1" followed by
"### Objectives"), so an empty-bodied heading also labels the same-level sections
after it, until the next group heading at that level or any higher-level heading.
A numbered empty heading always opens a group; an unnumbered one opens a group only
when no numbered group is open at that level, because inside a numbered group an
empty "### Key activities" is a sub-heading, not a new group. The path is used as embedding context and returned with reads; it stays a
list of spans, so no heading text is stored.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from .base import Extractor, trim_bounds, window_indices

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)
# "1. Phase 1: ...", "2) Setup", "Step 3: ..." style headings that open a numbered group.
_NUMBERED = re.compile(r"^(\d+[.)]\s|(step|phase|stage|part)\s+\d+\b)", re.IGNORECASE)


class MarkdownExtractor(Extractor):
    name = "markdown"

    def ranges(self, text: str, c2b: Sequence[int]) -> list[tuple[int, int, dict[str, Any]]]:
        matches = list(_HEADING.finditer(text))

        # (section_start, section_end, title_start, title_end, level, body_is_empty);
        # -1 titles and level 0 mean "no heading".
        sections: list[tuple[int, int, int, int, int, bool]] = []
        if not matches:
            sections.append((0, len(text), -1, -1, 0, False))
        else:
            if matches[0].start() > 0:
                sections.append((0, matches[0].start(), -1, -1, 0, False))
            for i, m in enumerate(matches):
                end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
                t0, t1 = trim_bounds(text, m.start(2), m.end(2))
                empty = not text[m.end() : end].strip()
                sections.append((m.start(), end, t0, t1, len(m.group(1)), empty))

        out: list[tuple[int, int, dict[str, Any]]] = []
        ancestors: list[tuple[int, dict[str, int]]] = []  # (level, title span), outermost first
        labels: dict[int, dict[str, int]] = {}            # level -> empty-bodied group heading
        numbered: dict[int, bool] = {}                     # level -> whether that group is numbered
        for index, (s, e, t0, t1, level, empty) in enumerate(sections):
            meta: dict[str, Any] = {"section": index}
            if t0 >= 0 and t1 > t0:
                title = {"start": c2b[t0], "end": c2b[t1]}
                meta["heading_span"] = title
                while ancestors and ancestors[-1][0] >= level:
                    ancestors.pop()
                for deeper in [lv for lv in labels if lv > level]:
                    del labels[deeper]
                    numbered.pop(deeper, None)
                is_numbered = bool(_NUMBERED.match(text[t0:t1]))
                opens_group = empty and (is_numbered or not numbered.get(level, False))
                if opens_group:
                    labels[level], numbered[level] = title, is_numbered
                label = [] if opens_group or level not in labels else [labels[level]]
                meta["heading_path"] = [span for _, span in ancestors] + label + [title]
                ancestors.append((level, title))
            for w0, w1 in window_indices(text, s, e, self.chunk_size, self.chunk_overlap):
                out.append((w0, w1, dict(meta)))
        return out
