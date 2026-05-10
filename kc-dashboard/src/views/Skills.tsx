import { useState, useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  listSkills, getSkill, getSkillFile,
  type Skill, type SkillDetail,
} from "../api/skills";


function PlatformPill({ platform }: { platform: string }) {
  const label = platform === "macos" ? "MAC" : platform === "linux" ? "LIN" : platform === "windows" ? "WIN" : platform.toUpperCase();
  return (
    <span className="px-1.5 py-0.5 text-[9px] tracking-[0.1em] uppercase border border-line text-muted2 font-mono">
      {label}
    </span>
  );
}


function SkillRow({ s, expanded, onToggle }: {
  s: Skill;
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <li className="font-mono text-xs">
      <div className="flex items-center gap-3 py-2 cursor-pointer hover:bg-panel2"
           onClick={onToggle}>
        <span className="font-semibold text-text">{s.name}</span>
        {s.category && (
          <span className="px-1.5 py-0.5 text-[9px] tracking-[0.1em] uppercase border border-accent text-accent">
            {s.category}
          </span>
        )}
        <span className="flex-1 text-muted truncate" title={s.description}>{s.description}</span>
        {s.tags.map(t => (
          <span key={t} className="px-1.5 py-0.5 text-[9px] tracking-[0.1em] uppercase border border-line text-muted">{t}</span>
        ))}
        {s.platforms?.map(p => <PlatformPill key={p} platform={p} />)}
      </div>
      {expanded && <SkillDetailPanel name={s.name} />}
    </li>
  );
}


function SkillDetailPanel({ name }: { name: string }) {
  const q = useQuery({ queryKey: ["skill", name], queryFn: () => getSkill(name) });
  if (q.isLoading) return <div className="px-4 py-3 text-muted text-[11px]">Loading…</div>;
  if (q.isError || !q.data) return <div className="px-4 py-3 text-bad text-[11px]">Failed to load.</div>;
  const d = q.data;
  return (
    <div className="bg-panel2 border-l-2 border-accent px-4 py-3 mb-2 text-muted text-[11px] space-y-3">
      <pre className="text-text whitespace-pre-wrap font-mono">{d.body}</pre>
      <div className="grid grid-cols-[120px_1fr] gap-x-3 gap-y-1">
        {d.version && (<><div className="text-muted2">version</div><div>{d.version}</div></>)}
        {d.tags.length > 0 && (<><div className="text-muted2">tags</div><div>{d.tags.join(", ")}</div></>)}
        {d.related_skills.length > 0 && (<><div className="text-muted2">related</div><div>{d.related_skills.join(", ")}</div></>)}
        <div className="text-muted2">skill_dir</div>
        <div className="break-all">{d.skill_dir}</div>
      </div>
      <SupportingFilesList name={name} files={d.supporting_files} />
      <div className="border-t border-line pt-2">
        <div className="text-muted2 mb-1">Invoke from chat:</div>
        <input
          type="text"
          readOnly
          value={`/${name} `}
          className="w-full bg-bg border border-line px-2 py-1 text-text font-mono text-xs"
          onFocus={(e) => e.currentTarget.select()}
        />
      </div>
    </div>
  );
}


function SupportingFilesList({ name, files }: {
  name: string;
  files: SkillDetail["supporting_files"];
}) {
  const [openFile, setOpenFile] = useState<string | null>(null);
  const fileQ = useQuery({
    queryKey: ["skill-file", name, openFile],
    queryFn: () => openFile ? getSkillFile(name, openFile) : Promise.reject("no file"),
    enabled: openFile !== null,
  });
  const sections = (["references", "templates", "scripts", "assets"] as const)
    .filter(k => files[k].length > 0);
  if (sections.length === 0) return null;
  return (
    <div className="space-y-1">
      {sections.map(k => (
        <div key={k}>
          <div className="text-muted2 uppercase tracking-[0.1em] text-[10px] mt-2">{k}</div>
          <ul className="ml-3">
            {files[k].map(f => {
              const path = `${k}/${f}`;
              return (
                <li key={path}>
                  <button
                    onClick={(e) => { e.stopPropagation(); setOpenFile(path === openFile ? null : path); }}
                    className="text-text hover:text-accent text-left font-mono">
                    {f}
                  </button>
                  {openFile === path && fileQ.data && (
                    <pre className="bg-bg border border-line p-2 mt-1 mb-2 whitespace-pre-wrap font-mono text-[11px] text-text overflow-x-auto">
                      {fileQ.data.content}
                    </pre>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </div>
  );
}


export default function Skills() {
  const [params, setParams] = useSearchParams();
  const activeCategories = params.getAll("category");
  const [search, setSearch] = useState("");
  const [expandedName, setExpandedName] = useState<string | null>(null);

  const q = useQuery({
    queryKey: ["skills"],
    queryFn: listSkills,
    refetchInterval: 30_000,
  });

  const allCategories = useMemo(() => {
    const set = new Set<string>();
    (q.data?.skills ?? []).forEach(s => { if (s.category) set.add(s.category); });
    return Array.from(set).sort();
  }, [q.data]);

  const filtered = useMemo(() => {
    let out = q.data?.skills ?? [];
    if (activeCategories.length > 0) {
      out = out.filter(s => s.category !== null && activeCategories.includes(s.category));
    }
    if (search.trim()) {
      const needle = search.toLowerCase();
      out = out.filter(s =>
        s.name.toLowerCase().includes(needle) ||
        s.description.toLowerCase().includes(needle));
    }
    return out;
  }, [q.data, activeCategories.join(","), search]);

  const toggleCategory = (c: string) => {
    const cur = params.getAll("category");
    params.delete("category");
    const next = cur.includes(c) ? cur.filter(x => x !== c) : [...cur, c];
    next.forEach(v => params.append("category", v));
    setParams(params, { replace: true });
  };

  return (
    <div className="p-5">
      {q.isError && (
        <div className="text-bad text-sm mb-3">Failed to load skills.</div>
      )}

      <div className="flex flex-wrap items-center gap-2 mb-3">
        <span className="text-xs uppercase tracking-[0.12em] text-muted2 mr-1">Category</span>
        {allCategories.map(c => (
          <button key={c}
                  onClick={() => toggleCategory(c)}
                  className={"px-3 py-1 text-xs border "
                    + (activeCategories.includes(c) ? "border-accent text-text" : "border-line text-muted hover:text-text")}>
            {c}
          </button>
        ))}
        <input
          type="text"
          placeholder="Search…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="ml-auto bg-bg border border-line px-3 py-1 text-xs text-text"
        />
      </div>

      {q.isLoading && <div className="text-muted text-sm">Loading…</div>}
      {!q.isLoading && filtered.length === 0 && (
        <div className="text-muted text-sm py-8 text-center">
          {(q.data?.skills?.length ?? 0) === 0
            ? "No skills yet. Drop a SKILL.md under ~/KonaClaw/skills/<category>/<skill>/."
            : "No skills match these filters."}
        </div>
      )}

      <ul className="divide-y divide-line">
        {filtered.map(s => (
          <SkillRow
            key={s.name}
            s={s}
            expanded={expandedName === s.name}
            onToggle={() => setExpandedName(expandedName === s.name ? null : s.name)}
          />
        ))}
      </ul>
    </div>
  );
}
