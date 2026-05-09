import type { ConnectorSummary } from "../../api/connectors";

const ICONS: Record<ConnectorSummary["name"], string> = {
  telegram: "📱", imessage: "💬", gmail: "📧", calendar: "📅", zapier: "⚡",
};

type Props = {
  items: ConnectorSummary[];
  selected: string;
  onSelect: (name: string) => void;
};

export default function ConnectorList({ items, selected, onSelect }: Props) {
  return (
    <div className="p-2 space-y-1">
      {items.map((c) => {
        const tone = c.status === "connected" ? "bg-good"
          : c.status === "unavailable" ? "bg-line"
          : c.status === "error" ? "bg-bad"
          : "bg-muted";
        return (
          <button
            key={c.name}
            onClick={() => onSelect(c.name)}
            className={"w-full flex items-center justify-between px-3 py-2 rounded text-sm "
              + (selected === c.name ? "bg-panel border border-accent" : "hover:bg-panel border border-transparent")}
          >
            <span className="flex items-center gap-2">
              <span>{ICONS[c.name]}</span>
              <span className="capitalize">{c.name}</span>
            </span>
            <span className={"w-2 h-2 rounded-full " + tone}></span>
          </button>
        );
      })}
    </div>
  );
}
