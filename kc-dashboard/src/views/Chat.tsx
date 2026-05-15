import { useState, useMemo, useEffect, useRef } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { listAgents, createConversation } from "../api/agents";
import {
  listConversations,
  listMessages,
  setConversationPinned,
  setConversationTitle,
  deleteConversation,
} from "../api/conversations";
import { useChatSocket, type ChatEvent } from "../ws/useChatSocket";
import { useLiveTokensPerSecond } from "../ws/useLiveTokensPerSecond";
import { useWS } from "../ws/WSContext";
import { useApprovals } from "../store/approvals";
import { MessageBubble, type BubbleUsage } from "../components/MessageBubble";
import { ApprovalCard } from "../components/ApprovalCard";
import { ThinkingIndicator } from "../components/ThinkingIndicator";
import { NewsWidget } from "../components/NewsWidget";
import { TodoWidget } from "../components/TodoWidget";
import { ClarifyCard } from "../components/ClarifyCard";
import { SubagentTraceBlock } from "../components/SubagentTraceBlock";
import { AttachmentChip } from "../components/AttachmentChip";
import { useAttachmentUpload } from "../hooks/useAttachmentUpload";
import { listConversationSubagentRuns, type SubagentRunRow } from "../api/subagents";
import { formatTokensPerSecond, formatTokenCount, formatTtfb } from "../lib/formatUsage";

type SubagentStartedEvent = Extract<ChatEvent, { type: "subagent_started" }>;
type SubagentToolEvent = Extract<ChatEvent, { type: "subagent_tool" }>;
type SubagentFinishedEvent = Extract<ChatEvent, { type: "subagent_finished" }>;

const padNum = (n: number, len = 3) => String(n).padStart(len, "0");

export default function Chat() {
  const [searchParams, setSearchParams] = useSearchParams();
  const activeAgent = searchParams.get("agent");
  const activeConv = searchParams.get("conv") ? Number(searchParams.get("conv")) : null;
  const qc = useQueryClient();

  const setActiveAgent = (name: string | null) => {
    const next = new URLSearchParams(searchParams);
    if (name) next.set("agent", name);
    else next.delete("agent");
    next.delete("conv");
    setSearchParams(next, { replace: true });
  };
  const setActiveConv = (cid: number | null) => {
    const next = new URLSearchParams(searchParams);
    if (cid != null) next.set("conv", String(cid));
    else next.delete("conv");
    setSearchParams(next, { replace: true });
  };

  const agentsQ = useQuery({ queryKey: ["agents"], queryFn: listAgents });
  const convsQ = useQuery({
    queryKey: ["conversations", activeAgent],
    queryFn: () => listConversations(activeAgent || undefined),
    enabled: !!activeAgent,
  });
  const [awaitingReply, setAwaitingReply] = useState(false);
  const msgsQ = useQuery({
    queryKey: ["messages", activeConv],
    queryFn: () => listMessages(activeConv!),
    enabled: activeConv != null,
    refetchInterval: awaitingReply ? 1500 : false,
  });
  const runsQ = useQuery({
    queryKey: ["conv-subagent-runs", activeConv],
    queryFn: () => listConversationSubagentRuns(activeConv!),
    enabled: activeConv != null,
  });

  const newConv = useMutation({
    mutationFn: () => createConversation(activeAgent!),
    onSuccess: ({ conversation_id }) => {
      setActiveConv(conversation_id);
      qc.invalidateQueries({ queryKey: ["conversations"] });
    },
  });
  const pinConv = useMutation({
    mutationFn: ({ cid, pinned }: { cid: number; pinned: boolean }) =>
      setConversationPinned(cid, pinned),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["conversations"] }),
  });
  const delConv = useMutation({
    mutationFn: (cid: number) => deleteConversation(cid),
    onSuccess: (_, cid) => {
      if (activeConv === cid) setActiveConv(null);
      qc.invalidateQueries({ queryKey: ["conversations"] });
    },
  });
  const renameConv = useMutation({
    mutationFn: ({ cid, title }: { cid: number; title: string }) =>
      setConversationTitle(cid, title),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["conversations"] }),
  });

  const [editingId, setEditingId] = useState<number | null>(null);
  const [editDraft, setEditDraft] = useState("");

  const pendingApprovals = useApprovals((s) => s.pending);
  const pendingForAgent = useMemo(
    () => (activeAgent ? pendingApprovals.filter((p) => p.agent === activeAgent) : []),
    [pendingApprovals, activeAgent],
  );
  const resolveLocalApproval = useApprovals((s) => s.resolveLocal);
  const { send: wsSend } = useWS();
  const respondToApproval = (id: string, allowed: boolean) => {
    wsSend({
      type: "approval_response",
      request_id: id,
      allowed,
      reason: allowed ? null : "user denied",
    });
    resolveLocalApproval(id);
  };

  const { events, sendUserMessage } = useChatSocket(activeConv);
  const [draft, setDraft] = useState("");
  const {
    chips,
    addFiles,
    remove: removeChip,
    clear: clearChips,
    allReady,
    readyAttachmentIds,
  } = useAttachmentUpload(activeConv);
  const [dragActive, setDragActive] = useState(false);
  const dropRootRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  // Per-message reasoning toggle. Defaults off — gemma4 and other reasoning
  // models over-think trivial prompts; user opts in when depth is wanted.
  // Only honored by the supervisor when the agent's model is reasoning-capable
  // and the Ollama base_url is local (native /api/chat).
  const [thinkOn, setThinkOn] = useState(false);

  const [pendingClarifies, setPendingClarifies] = useState<Array<{
    request_id: string; question: string; choices: string[];
    timeout_seconds: number; started_at: number;
    resolved?: { choice: string | null; reason?: string };
  }>>([]);

  useEffect(() => {
    if (events.length === 0) return;
    const last = events[events.length - 1];
    if (last?.type === "clarify_request") {
      setPendingClarifies((prev) => {
        if (prev.find((p) => p.request_id === last.request_id)) return prev;
        return [...prev, {
          request_id:      last.request_id,
          question:        last.question,
          choices:         last.choices,
          timeout_seconds: last.timeout_seconds,
          started_at:      last.started_at,
        }];
      });
    }
  }, [events]);

  const respondToClarify = (request_id: string, choice: string | null, reason?: string) => {
    sendUserMessage({
      type: "clarify_response",
      request_id,
      choice,
      ...(reason ? { reason } : {}),
    });
    // Mark resolved instead of removing — card stays in transcript as history.
    setPendingClarifies((prev) => prev.map((p) =>
      p.request_id === request_id
        ? { ...p, resolved: { choice, reason } }
        : p
    ));
  };

  const [todoEventCounter, setTodoEventCounter] = useState(0);

  useEffect(() => {
    if (events.length === 0) return;
    const last = events[events.length - 1];
    if (last?.type === "todo_event") {
      setTodoEventCounter((c) => c + 1);
    }
  }, [events]);

  // Group subagent WS frames by subagent_id for inline trace rendering.
  const traceGroups = useMemo(() => {
    const groups = new Map<string, {
      started: SubagentStartedEvent | null;
      tools: SubagentToolEvent[];
      finished: SubagentFinishedEvent | null;
    }>();
    for (const ev of events) {
      if (
        ev.type === "subagent_started" ||
        ev.type === "subagent_tool" ||
        ev.type === "subagent_approval" ||
        ev.type === "subagent_finished"
      ) {
        const g = groups.get(ev.subagent_id) ?? { started: null, tools: [], finished: null };
        if (ev.type === "subagent_started") g.started = ev;
        else if (ev.type === "subagent_tool") g.tools.push(ev);
        else if (ev.type === "subagent_finished") g.finished = ev;
        groups.set(ev.subagent_id, g);
      }
    }
    return groups;
  }, [events]);

  // Auto-scroll the transcript to the bottom when new content arrives.
  // Refs into the rendered <div> below; pinned to the bottom unless the user
  // has scrolled up to read history (then we leave them put).
  const transcriptRef = useRef<HTMLDivElement>(null);
  const wasNearBottomRef = useRef(true);
  const handleTranscriptScroll = () => {
    const el = transcriptRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    wasNearBottomRef.current = distanceFromBottom < 120;
  };

  // Always snap when the conversation switches, regardless of prior position.
  useEffect(() => {
    const el = transcriptRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
    wasNearBottomRef.current = true;
  }, [activeConv]);

  useEffect(() => {
    const last = events[events.length - 1];
    if (last?.type === "assistant_complete" && activeConv != null) {
      qc.invalidateQueries({ queryKey: ["messages", activeConv] });
      setAwaitingReply(false);
    }
  }, [events, activeConv, qc]);

  useEffect(() => {
    if (!awaitingReply) return;
    const last = msgsQ.data?.messages[msgsQ.data.messages.length - 1];
    if (last?.type === "AssistantMessage") setAwaitingReply(false);
  }, [msgsQ.data, awaitingReply]);

  // Per-bubble usage map (from persisted messages query).
  const bubbleUsageByIdx = useMemo(() => {
    const map = new Map<number, BubbleUsage>();
    const msgs = msgsQ.data?.messages ?? [];
    let assistantIdx = 0;
    for (const m of msgs as Array<{ type: string; usage?: BubbleUsage }>) {
      if (m.type === "AssistantMessage") {
        if (m.usage) map.set(assistantIdx, m.usage);
        assistantIdx++;
      }
    }
    return map;
  }, [msgsQ.data]);

  const rendered = useMemo(() => {
    const out: {
      role: "user" | "assistant";
      content: string;
      usage?: BubbleUsage;
      scheduled_job_id?: number | null;
    }[] = [];
    let assistantIdx = 0;
    for (const m of msgsQ.data?.messages ?? []) {
      if (m.type === "UserMessage") out.push({ role: "user", content: m.content ?? "" });
      else if (m.type === "AssistantMessage") {
        const content = m.content ?? "";
        // Hide silent intermediate steps — a Complete frame fires for every
        // tool-using turn, but the AssistantMessage row is empty when the
        // model called a tool without surrounding text. Showing those as
        // "(no reply...)" placeholders during an in-flight multi-step turn
        // is misleading.
        if (!content.trim()) { assistantIdx++; continue; }
        out.push({
          role: "assistant",
          content,
          usage: bubbleUsageByIdx.get(assistantIdx),
          scheduled_job_id: m.scheduled_job_id ?? null,
        });
        assistantIdx++;
      }
    }
    return out;
  }, [msgsQ.data, bubbleUsageByIdx]);

  // Tokens streamed since the most recent `assistant_complete`. Rendered as a
  // ghost assistant bubble so the user sees progress instead of dead air. The
  // bubble disappears once `msgsQ` refetches the persisted message.
  const streaming = useMemo(() => {
    let start = 0;
    for (let i = events.length - 1; i >= 0; i--) {
      if (events[i].type === "assistant_complete") { start = i + 1; break; }
    }
    let buf = "";
    for (let i = start; i < events.length; i++) {
      const e = events[i];
      if (e.type === "token") buf += e.delta;
    }
    return buf;
  }, [events]);

  // Reasoning ("thinking") deltas accumulated alongside the streaming bubble.
  // Reasoning models (gemma4, deepseek-r1, qwq) emit these before content.
  // Ephemeral: not persisted, disappears when assistant_complete fires and
  // the bubble is replaced by the persisted MessageBubble.
  const streamingReasoning = useMemo(() => {
    let start = 0;
    for (let i = events.length - 1; i >= 0; i--) {
      if (events[i].type === "assistant_complete") { start = i + 1; break; }
    }
    let buf = "";
    for (let i = start; i < events.length; i++) {
      const e = events[i];
      if (e.type === "reasoning") buf += e.delta;
    }
    return buf;
  }, [events]);

  // Build an ordered list interleaving messages and trace blocks by timestamp.
  // Three sources:
  //   1. persisted messages (with ts from API)
  //   2. `runsQ` (persisted subagent runs with started_ts)
  //   3. `traceGroups` (live WS runs — placed after persisted items since no ts)
  // Dedupe rule: live run (traceGroups) wins over persisted run with same id.
  type MessageItem = {
    role: "user" | "assistant";
    content: string;
    usage?: BubbleUsage;
    scheduled_job_id?: number | null;
  };
  type TraceShape = {
    started: { subagent_id: string; template: string; label?: string | null; task_preview: string };
    tools: Array<{ tool: string; args_preview?: string; result_preview?: string; tier?: string }>;
    finished: {
      status: "ok" | "error" | "timeout" | "stopped" | "interrupted";
      reply_preview: string;
      duration_ms: number;
      tool_calls_used: number;
      error_message?: string | null;
    } | null;
  };
  type TranscriptItem =
    | { kind: "message"; data: MessageItem }
    | { kind: "trace"; subagentId: string; trace: TraceShape; onStop?: () => void };

  const orderedTranscript = useMemo((): TranscriptItem[] => {
    const msgs = msgsQ.data?.messages ?? [];
    const persistedRuns = runsQ.data?.runs ?? [];
    // Set of live subagent ids (from WS events).
    const liveIds = new Set(traceGroups.keys());

    // Build message sortable items with ts for ordering.
    type Sortable = { _ts: number; item: TranscriptItem };
    const sortable: Sortable[] = [];
    let assistantIdx = 0;
    let tsFallback = 0;
    for (const m of msgs) {
      const ts: number = (m as { ts?: number }).ts ?? (tsFallback += 0.0001);
      if (m.type === "UserMessage") {
        sortable.push({
          _ts: ts,
          item: { kind: "message", data: { role: "user", content: m.content ?? "" } },
        });
      } else if (m.type === "AssistantMessage") {
        const content = m.content ?? "";
        if (!content.trim()) { assistantIdx++; continue; }
        sortable.push({
          _ts: ts,
          item: {
            kind: "message",
            data: {
              role: "assistant",
              content,
              usage: bubbleUsageByIdx.get(assistantIdx),
              scheduled_job_id: (m as { scheduled_job_id?: number | null }).scheduled_job_id ?? null,
            },
          },
        });
        assistantIdx++;
      }
    }

    // Add persisted trace items, skip those that have a live counterpart.
    for (const row of persistedRuns) {
      if (liveIds.has(row.id)) continue;
      sortable.push({
        _ts: row.started_ts,
        item: {
          kind: "trace",
          subagentId: row.id,
          trace: {
            started: {
              subagent_id: row.id,
              template: row.template,
              label: row.label,
              task_preview: row.task_preview ?? "",
            },
            tools: row.tools.map((t) => ({
              tool: t.tool,
              args_preview: t.args_json,
              result_preview: t.result ?? "",
              tier: t.decision,
            })),
            finished: row.status === "running" ? null : {
              status: row.status as "ok" | "error" | "timeout" | "stopped" | "interrupted",
              reply_preview: row.reply_text ?? "",
              duration_ms: row.duration_ms ?? 0,
              tool_calls_used: row.tool_calls_used,
              error_message: row.error_message,
            },
          },
          onStop: undefined,
        },
      });
    }

    sortable.sort((a, b) => a._ts - b._ts);
    const merged: TranscriptItem[] = sortable.map((s) => s.item);

    // Append live trace blocks at the end (no stable ts; these are in-flight).
    for (const [subagentId, group] of traceGroups.entries()) {
      if (!group.started) continue;
      merged.push({
        kind: "trace",
        subagentId,
        trace: {
          started: group.started,
          tools: group.tools,
          finished: group.finished,
        },
        onStop: () => sendUserMessage({ type: "subagent_stop", subagent_id: subagentId }),
      });
    }

    return merged;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [msgsQ.data, runsQ.data, traceGroups, bubbleUsageByIdx, sendUserMessage]);

  // Auto-scroll to bottom when content changes — only if the user is already
  // near the bottom (so we don't yank them away when they've scrolled up to
  // read history).
  useEffect(() => {
    const el = transcriptRef.current;
    if (!el) return;
    if (wasNearBottomRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [orderedTranscript, streaming, awaitingReply, pendingForAgent]);

  const firstTokenAtRef = useRef<number | null>(null);
  useEffect(() => {
    const last = events[events.length - 1];
    if (last?.type === "token" && firstTokenAtRef.current == null) {
      firstTokenAtRef.current = Date.now();
    }
    if (last?.type === "assistant_complete") {
      firstTokenAtRef.current = null;
    }
  }, [events]);

  // Reset first-token timestamp when the user switches conversations.
  useEffect(() => { firstTokenAtRef.current = null; }, [activeConv]);

  const liveTps = useLiveTokensPerSecond(streaming, firstTokenAtRef.current);

  // Most recent {type:"usage"} event since the last assistant_complete.
  const currentTurnUsage = useMemo(() => {
    let resetAt = 0;
    for (let i = events.length - 1; i >= 0; i--) {
      if (events[i].type === "assistant_complete") { resetAt = i + 1; break; }
    }
    for (let i = events.length - 1; i >= resetAt; i--) {
      const e = events[i];
      if (e.type === "usage") return e;
    }
    return null;
  }, [events]);


  const activeConvData = activeConv != null
    ? convsQ.data?.conversations.find((c) => c.id === activeConv) ?? null
    : null;
  const activeAgentData = activeAgent
    ? agentsQ.data?.agents.find((a) => a.name === activeAgent) ?? null
    : null;

  return (
    <div
      ref={dropRootRef}
      data-testid="chat-root"
      className="flex h-full overflow-hidden"
      style={{ position: "relative" }}
      onDragEnter={(e) => {
        e.preventDefault();
        if (e.dataTransfer?.types && Array.from(e.dataTransfer.types).includes("Files")) {
          setDragActive(true);
        }
      }}
      onDragOver={(e) => {
        e.preventDefault();
      }}
      onDragLeave={(e) => {
        if (e.target === dropRootRef.current) setDragActive(false);
      }}
      onDrop={async (e) => {
        e.preventDefault();
        setDragActive(false);
        const files = Array.from(e.dataTransfer?.files || []);
        if (files.length) await addFiles(files);
      }}
    >
      {dragActive && (
        <div
          className="chat-drop-overlay"
          style={{
            position: "absolute",
            inset: 0,
            zIndex: 100,
            background: "rgba(64, 96, 192, 0.15)",
            border: "2px dashed rgba(64, 96, 192, 0.7)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            pointerEvents: "none",
            fontSize: 18,
            fontWeight: 600,
            color: "rgba(40, 60, 140, 0.9)",
          }}
        >
          Drop to attach
        </div>
      )}
      {/* SIDEBAR — ROSTER */}
      <aside className="w-[304px] shrink-0 border-r border-line bg-panel overflow-y-auto pb-6">
        <div className="px-6 pt-5 pb-3 flex items-center justify-between">
          <div className="font-mono text-[10px] uppercase tracking-[0.22em] text-muted2 font-medium flex items-center gap-2.5">
            <span className="inline-block w-3.5 h-px bg-accent" />
            Roster
          </div>
          <div className="font-mono text-[10px] text-muted2">
            {padNum(agentsQ.data?.agents.length ?? 0, 2)}/{padNum(agentsQ.data?.agents.length ?? 0, 2)}
          </div>
        </div>

        {agentsQ.data?.agents.map((a) => {
          const isActive = activeAgent === a.name;
          return (
            <div
              key={a.name}
              className={`mx-4 mb-2 border bg-panel2 cursor-pointer transition-all relative ${
                isActive
                  ? "border-accent bg-panel3 shadow-card-active"
                  : "border-line hover:border-lineBright"
              }`}
              onClick={() => setActiveAgent(a.name)}
            >
              {isActive && <div className="absolute -left-[5px] -top-px -bottom-px w-[3px] bg-accent" />}
              <div className="px-4 py-3 flex items-start justify-between gap-2.5">
                <div className="min-w-0">
                  <div className="font-display font-semibold text-[18px] leading-[1.1] text-textStrong [letter-spacing:-0.01em] truncate">
                    {a.name}
                  </div>
                  <div className="font-mono text-[9.5px] text-muted uppercase tracking-[0.08em] mt-1.5">
                    {a.model}
                    <span className="text-muted2 mx-1.5">·</span>
                    {a.status}
                  </div>
                </div>
                <div
                  className={`shrink-0 w-1.5 h-1.5 mt-2 ${
                    a.status === "degraded" ? "bg-warn" : "bg-good"
                  }`}
                  style={{
                    boxShadow:
                      a.status === "degraded"
                        ? "0 0 6px rgb(var(--warn) / 0.6)"
                        : "0 0 6px rgb(var(--ok) / 0.6)",
                  }}
                />
              </div>

              {isActive && (
                <div className="border-t border-line bg-panel">
                  {convsQ.data?.conversations.length === 0 && (
                    <div className="px-4 py-3 text-[12px] text-muted italic">No prior chats</div>
                  )}
                  {convsQ.data?.conversations.map((c, idx) => {
                    const isCurrent = activeConv === c.id;
                    const fallback = `${new Date(c.started_at * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}`;
                    const display = c.title?.trim() || fallback;
                    const isEditing = editingId === c.id;
                    const startEdit = () => { setEditingId(c.id); setEditDraft(c.title ?? ""); };
                    const commit = () => {
                      if (editDraft !== (c.title ?? "")) renameConv.mutate({ cid: c.id, title: editDraft });
                      setEditingId(null);
                    };
                    const isLast = idx === (convsQ.data?.conversations.length ?? 0) - 1;
                    return (
                      <div
                        key={c.id}
                        onClick={(e) => {
                          e.stopPropagation();
                          if (!isEditing) setActiveConv(c.id);
                        }}
                        onDoubleClick={(e) => { e.stopPropagation(); startEdit(); }}
                        className={`group relative px-4 py-2 flex items-center gap-3 transition-colors ${
                          isLast ? "" : "border-b border-dashed border-line"
                        } ${
                          isCurrent
                            ? "bg-accent/[0.12] text-textStrong"
                            : "text-muted hover:text-text hover:bg-panel2"
                        } ${isEditing ? "" : "cursor-pointer"}`}
                      >
                        {isCurrent && (
                          <span className="absolute left-0 top-1/2 -translate-y-1/2 w-2 h-px bg-accent" />
                        )}
                        <span
                          className={`font-mono text-[10px] tracking-[0.04em] font-medium shrink-0 ${
                            isCurrent ? "text-accent" : "text-muted2"
                          }`}
                        >
                          DWG·{padNum(c.id)}
                        </span>
                        {isEditing ? (
                          <input
                            autoFocus
                            value={editDraft}
                            onChange={(e) => setEditDraft(e.target.value)}
                            onClick={(e) => e.stopPropagation()}
                            onBlur={commit}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") { e.preventDefault(); commit(); }
                              else if (e.key === "Escape") { e.preventDefault(); setEditingId(null); }
                            }}
                            placeholder={fallback}
                            className="flex-1 bg-bgDeep border border-line rounded-none px-2 py-0.5 text-[13.5px] text-textStrong outline-none font-body focus:border-accent"
                          />
                        ) : (
                          <span className="flex-1 font-body text-[13.5px] leading-[1.35] truncate" title="Double-click to rename">
                            {display}
                          </span>
                        )}
                        {!isEditing && (
                          <div className="flex items-center gap-1 shrink-0">
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                pinConv.mutate({ cid: c.id, pinned: !c.pinned });
                              }}
                              title={c.pinned ? "Unpin" : "Pin"}
                              className={`text-[12px] leading-none w-5 ${
                                c.pinned ? "text-accent opacity-100" : "opacity-0 group-hover:opacity-60 hover:!opacity-100"
                              }`}
                            >
                              ★
                            </button>
                            <button
                              type="button"
                              onClick={(e) => { e.stopPropagation(); startEdit(); }}
                              title="Rename"
                              className="text-[11px] leading-none w-4 opacity-0 group-hover:opacity-60 hover:!opacity-100"
                            >
                              ✎
                            </button>
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                if (confirm(`Delete "${display}"? This removes its messages permanently.`)) {
                                  delConv.mutate(c.id);
                                }
                              }}
                              title="Delete"
                              className="text-[11px] leading-none w-4 opacity-0 group-hover:opacity-60 hover:!opacity-100 hover:text-bad"
                            >
                              ✕
                            </button>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </aside>

      {/* CHAT PANE */}
      <section className="flex-1 flex flex-col min-w-0 min-h-0 bg-bg">
        {/* CHAT HEADER */}
        <header
          className="shrink-0 px-10 pt-4 pb-3 border-b border-line"
          style={{ background: "linear-gradient(to bottom, rgb(var(--panel)) 0%, rgb(var(--bg)) 100%)" }}
        >
          <div className="flex items-center justify-between gap-6 flex-wrap">
            <div className="flex flex-col gap-1 min-w-0 flex-1">
              {activeConv != null && (
                <div className="font-mono text-[9px] uppercase tracking-[0.2em] text-accent flex items-center gap-2">
                  <span className="inline-block w-3 h-px bg-accent opacity-60" />
                  <span>DWG · {padNum(activeConv)} / {activeAgentData?.status === "degraded" ? "Degraded" : "Active"}</span>
                  <span className="inline-block w-3 h-px bg-accent opacity-60" />
                </div>
              )}
              <div className="flex items-center gap-4 flex-wrap">
                <h1
                  className="font-display font-bold leading-[1] [letter-spacing:-0.025em] text-textStrong"
                  style={{ fontSize: "clamp(22px, 2.6vw, 28px)" }}
                >
                  {activeAgent ?? "Pick an agent"}
                </h1>
                {activeAgent && (
                  <button
                    type="button"
                    onClick={() => newConv.mutate()}
                    className="bg-transparent border border-accent text-accent hover:bg-accent hover:text-bgDeep font-mono text-[10px] uppercase tracking-[0.16em] font-bold px-[12px] py-[5px] inline-flex items-center gap-2 leading-none transition-colors"
                  >
                    <span className="font-display font-semibold text-base leading-none">+</span>
                    <span>New drawing</span>
                  </button>
                )}
              </div>
            </div>

            {activeConv != null && (
              <dl
                className="grid grid-cols-[auto_auto] gap-y-1 gap-x-5 px-3 py-2 border border-line bg-panel font-mono text-[9px] uppercase tracking-[0.1em] shrink-0"
              >
                <dt className="text-muted2 font-normal">Subject</dt>
                <dd className="text-text font-medium">
                  {activeConvData?.title?.trim() || "—"}
                </dd>
                {activeConvData && (
                  <>
                    <dt className="text-muted2 font-normal">Started</dt>
                    <dd className="text-text font-medium">
                      {new Date(activeConvData.started_at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                    </dd>
                  </>
                )}
                <dt className="text-muted2 font-normal">Msgs</dt>
                <dd className="text-text font-medium">{padNum(rendered.length, 2)}</dd>
                {activeAgentData && (
                  <>
                    <dt className="text-muted2 font-normal">Model</dt>
                    <dd className="text-text font-medium">{activeAgentData.model}</dd>
                    <dt className="text-muted2 font-normal">Status</dt>
                    <dd className="text-text font-medium">{activeAgentData.status}</dd>
                    <dt className="text-muted2 font-normal">Last reply</dt>
                    <dd className="text-text font-medium">
                      {(() => {
                        if (currentTurnUsage && currentTurnUsage.usage_reported && currentTurnUsage.output_tokens != null && currentTurnUsage.generation_ms >= 50) {
                          const tps = (currentTurnUsage.output_tokens * 1000) / currentTurnUsage.generation_ms;
                          return `${formatTokensPerSecond(tps)} · ${formatTokenCount(currentTurnUsage.output_tokens)}`;
                        }
                        if (currentTurnUsage && !currentTurnUsage.usage_reported && liveTps != null) {
                          return `~${formatTokensPerSecond(liveTps)} · estimate`;
                        }
                        if (streaming && liveTps != null) {
                          return `~${formatTokensPerSecond(liveTps)} · streaming`;
                        }
                        return "—";
                      })()}
                    </dd>
                    {currentTurnUsage && (
                      <>
                        <dt className="text-muted2 font-normal">TTFB</dt>
                        <dd className="text-muted font-medium">
                          {formatTtfb(currentTurnUsage.ttfb_ms)}
                          {currentTurnUsage.calls > 1 && ` · ${currentTurnUsage.calls} calls`}
                        </dd>
                      </>
                    )}
                  </>
                )}
              </dl>
            )}
          </div>
        </header>

        {/* TRANSCRIPT */}
        <div
          ref={transcriptRef}
          onScroll={handleTranscriptScroll}
          className="flex-1 min-h-0 overflow-y-auto px-10 py-6 divide-y divide-line"
        >
          {!activeAgent && (
            <div className="py-16 text-center">
              <div className="font-display font-semibold text-2xl text-textStrong mb-2 [letter-spacing:-0.02em]">
                Pick an agent
              </div>
              <div className="font-body text-base text-muted">Select one from the roster to begin.</div>
            </div>
          )}
          {activeAgent && activeConv == null && (
            <div className="py-16 text-center">
              <div className="font-display font-semibold text-2xl text-textStrong mb-2 [letter-spacing:-0.02em]">
                No drawing selected
              </div>
              <div className="font-body text-base text-muted">Open one from the sidebar or start a new drawing.</div>
            </div>
          )}
          {orderedTranscript.map((item, i) => {
            if (item.kind === "message") {
              const m = item.data;
              return m.role === "assistant" ? (
                <AssistantBubble
                  key={`msg-${i}`}
                  content={m.content}
                  usage={m.usage}
                  scheduled_job_id={m.scheduled_job_id}
                />
              ) : (
                <MessageBubble key={`msg-${i}`} role={m.role} content={m.content} usage={m.usage} />
              );
            }
            // kind === "trace"
            return (
              <SubagentTraceBlock
                key={`subagent-${item.subagentId}`}
                started={item.trace.started}
                tools={item.trace.tools}
                finished={item.trace.finished}
                onStop={item.onStop}
              />
            );
          })}
          {(streaming.trim() || streamingReasoning.trim()) && (
            <MessageBubble
              role="assistant"
              content={streaming}
              reasoning={streamingReasoning || undefined}
            />
          )}
          {pendingForAgent.map((req) => (
            <ApprovalCard
              key={req.request_id}
              req={req}
              onApprove={(id) => respondToApproval(id, true)}
              onDeny={(id) => respondToApproval(id, false)}
            />
          ))}
          {pendingClarifies.map((req) => (
            <ClarifyCard
              key={req.request_id}
              request_id={req.request_id}
              question={req.question}
              choices={req.choices}
              timeout_seconds={req.timeout_seconds}
              started_at={req.started_at}
              onChoose={(rid, c) => respondToClarify(rid, c)}
              onSkip={(rid) => respondToClarify(rid, null, "skipped")}
              resolved={req.resolved}
            />
          ))}
          {awaitingReply && pendingForAgent.length === 0 && !streaming && !streamingReasoning && (() => {
            const last = events[events.length - 1] as { type?: string; call?: { name?: string } } | undefined;
            let label = "thinking";
            if (last?.type === "tool_call" && last.call?.name) {
              label = `running ${last.call.name}`;
            }
            return <ThinkingIndicator label={label} />;
          })()}
        </div>

        {/* COMPOSER */}
        {activeConv != null && (
          <form
            className="shrink-0 px-10 py-5 border-t border-line bg-panel relative"
            onSubmit={(e) => {
              e.preventDefault();
              if (!draft.trim() && chips.length === 0) return;
              if (!allReady) return;
              sendUserMessage({
                type: "user_message",
                content: draft,
                think: thinkOn,
                attachment_ids: readyAttachmentIds,
              });
              qc.setQueryData(["messages", activeConv], (old: { messages: unknown[] } | undefined) => ({
                messages: [...(old?.messages ?? []), { type: "UserMessage", content: draft }],
              }));
              setAwaitingReply(true);
              setDraft("");
              clearChips();
            }}
          >
            <span className="absolute top-0 inset-x-0 h-px bg-accent opacity-60" />
            <div className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted font-medium mb-2.5 flex items-center gap-2.5">
              <span className="text-accent font-display font-bold text-base leading-none">▸</span>
              <span>Reply</span>
              <span className="ml-auto text-muted2">⌘ + ↵ to send</span>
            </div>
            {chips.length > 0 && (
              <div
                className="chat-attachment-chip-row"
                style={{
                  display: "flex",
                  flexWrap: "wrap",
                  gap: 6,
                  padding: "4px 0 8px 0",
                }}
              >
                {chips.map((c) => (
                  <AttachmentChip
                    key={c.localId}
                    status={c.status}
                    filename={c.filename}
                    sizeBytes={c.sizeBytes}
                    error={c.error}
                    onRemove={() => removeChip(c.localId)}
                  />
                ))}
              </div>
            )}
            <div className="flex gap-3 items-stretch">
              <input
                type="file"
                ref={fileInputRef}
                style={{ display: "none" }}
                multiple
                onChange={async (e) => {
                  const files = Array.from(e.target.files || []);
                  if (files.length) await addFiles(files);
                  e.target.value = "";
                }}
              />
              <button
                type="button"
                aria-label="attach files"
                onClick={() => fileInputRef.current?.click()}
                className="border border-line bg-transparent text-muted hover:text-accent hover:border-accent px-3 font-mono text-[14px] inline-flex items-center transition-colors"
              >
                📎
              </button>
              <input
                className="flex-1 bg-bgDeep border border-line px-[18px] py-3.5 text-[16px] font-body text-textStrong outline-none focus:border-accent focus:[box-shadow:0_0_0_1px_rgb(var(--accent))] transition-shadow placeholder:text-muted2"
                placeholder="Reply…"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onPaste={async (e) => {
                  const items = Array.from(e.clipboardData?.items || []);
                  const imgs = items
                    .filter((it) => it.kind === "file" && it.type.startsWith("image/"))
                    .map((it) => it.getAsFile())
                    .filter((f): f is File => !!f);
                  if (imgs.length) {
                    e.preventDefault();
                    await addFiles(imgs);
                  }
                }}
              />
              <button
                type="button"
                aria-pressed={thinkOn}
                onClick={() => setThinkOn((v) => !v)}
                title={thinkOn
                  ? "Reasoning ON — model will think before answering"
                  : "Reasoning OFF — model answers directly"}
                className={`border px-3 font-mono text-[11px] uppercase tracking-[0.16em] font-bold inline-flex items-center gap-1.5 transition-colors ${
                  thinkOn
                    ? "bg-accent text-bgDeep border-accent hover:bg-accentBright"
                    : "bg-transparent text-muted border-line hover:text-accent hover:border-accent"
                }`}
              >
                <span>Think</span>
              </button>
              <button
                type="submit"
                disabled={!allReady || (draft.trim() === "" && chips.length === 0)}
                className="bg-accent text-bgDeep border-none px-7 font-mono text-[11px] uppercase tracking-[0.18em] font-bold inline-flex items-center gap-2 hover:bg-accentBright transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <span>Send</span>
                <span className="text-base font-extrabold">→</span>
              </button>
            </div>
          </form>
        )}
      </section>
      <NewsWidget />
      {activeConv != null && activeAgent != null && (
        <TodoWidget conversationId={activeConv} agent={activeAgent} refreshKey={todoEventCounter} />
      )}
    </div>
  );
}

/**
 * Assistant chat bubble with an optional "from reminder #N" footer.
 *
 * Wraps MessageBubble so all existing role/content/usage rendering is
 * preserved verbatim. When the assistant message originated from a fired
 * scheduled reminder (the supervisor stamps `scheduled_job_id` onto the
 * persisted row), we append a small footer Link that navigates to the
 * Reminders tab with `?highlight=N` so the corresponding row scrolls into
 * view and pulses briefly.
 */
export function AssistantBubble({
  content,
  usage,
  scheduled_job_id,
}: {
  content: string;
  usage?: BubbleUsage;
  scheduled_job_id?: number | null;
}) {
  return (
    <div>
      <MessageBubble role="assistant" content={content} usage={usage} />
      {scheduled_job_id != null && (
        <div className="grid grid-cols-[90px_1fr] gap-7 -mt-3 pb-3">
          <div />
          <Link
            to={`/reminders?highlight=${scheduled_job_id}`}
            className="block text-[10px] uppercase tracking-[0.12em] text-muted2 hover:text-accent font-mono"
          >
            ↻ from reminder #{scheduled_job_id}
          </Link>
        </div>
      )}
    </div>
  );
}
