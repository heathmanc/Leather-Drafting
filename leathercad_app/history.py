"""Snapshot-based undo/redo.

The model is plain dataclasses serialisable to dicts, so the simplest robust
history is a stack of full ``Document.to_dict()`` snapshots. Coarser than
per-command undo, but correct for every kind of edit with almost no wiring.

``to_dict()`` builds a fresh plain-data graph every call -- no container is
shared with the live document, so the snapshot handed to ``reset``/``push`` is
already private and copying it again on the way IN is pure overhead (137 ms of
the 162 ms an edit cost on a dense document). Reads hand the stored snapshot
out as is, too: ``Document.from_dict`` copies the only containers it used to
keep by reference (a polygon's corner mask, the tracing underlay), so a
restored document shares nothing with the stack and the deep copy on the way
OUT -- 0.3-0.5 s of every undo on a big file -- is gone. Callers treat what
they get back as read-only.
"""

from __future__ import annotations

from typing import List, Optional


class History:
    def __init__(self, limit: int = 200):
        self._stack: List[dict] = []
        self._index: int = -1
        self._limit = limit

    def reset(self, state: dict) -> None:
        self._stack = [state]
        self._index = 0

    def push(self, state: dict) -> None:
        if 0 <= self._index < len(self._stack) and self._stack[self._index] == state:
            return  # nothing actually changed: keep any redo tail
        # drop the redo tail
        if self._index < len(self._stack) - 1:
            del self._stack[self._index + 1:]
        self._stack.append(state)
        if len(self._stack) > self._limit:
            self._stack.pop(0)
        self._index = len(self._stack) - 1

    def can_undo(self) -> bool:
        return self._index > 0

    def can_redo(self) -> bool:
        return self._index < len(self._stack) - 1

    def undo(self) -> Optional[dict]:
        if not self.can_undo():
            return None
        self._index -= 1
        return self._stack[self._index]

    def redo(self) -> Optional[dict]:
        if not self.can_redo():
            return None
        self._index += 1
        return self._stack[self._index]
