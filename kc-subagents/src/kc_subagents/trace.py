from __future__ import annotations

import threading
from collections import defaultdict


class TraceBuffer:
    """Per-parent-conversation buffer of in-flight subagent frames.

    Frames are appended as instances emit them and evicted (per subagent_id)
    when a 'subagent_finished' frame for that id arrives. snapshot() returns
    the still-buffered frames in append order; used on WS reconnect.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_conv: dict[str, list[dict]] = defaultdict(list)

    def append(self, conversation_id: str, frame: dict) -> None:
        with self._lock:
            self._by_conv[conversation_id].append(frame)
            if frame.get("type") == "subagent_finished":
                sid = frame.get("subagent_id")
                self._by_conv[conversation_id] = [
                    f for f in self._by_conv[conversation_id]
                    if f.get("subagent_id") != sid
                ]
                if not self._by_conv[conversation_id]:
                    self._by_conv.pop(conversation_id, None)

    def snapshot(self, conversation_id: str) -> list[dict]:
        with self._lock:
            return list(self._by_conv.get(conversation_id, []))
