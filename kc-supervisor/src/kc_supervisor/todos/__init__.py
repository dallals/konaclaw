"""KonaClaw todo tools (Phase C).

Conversation-internal task list. Items are scoped to a conversation by
default; passing persist=True at creation lifts an item to agent-scope.
Backed by the supervisor's SQLite. Wired into Kona via the assembly.
"""
