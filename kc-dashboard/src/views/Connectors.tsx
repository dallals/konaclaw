import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { listConnectors } from "../api/connectors";
import ConnectorList from "../components/connectors/ConnectorList";
import TelegramPanel from "../components/connectors/TelegramPanel";
import IMessagePanel from "../components/connectors/IMessagePanel";
import GooglePanel from "../components/connectors/GooglePanel";
import ZapierPanel from "../components/connectors/ZapierPanel";

export default function Connectors() {
  const [selected, setSelected] = useState<string>("telegram");
  const { data } = useQuery({
    queryKey: ["connectors"], queryFn: listConnectors, refetchInterval: 5000,
  });
  const items = data?.connectors ?? [];

  return (
    <div className="grid grid-cols-[220px_1fr] h-full">
      <aside className="border-r border-line bg-bg">
        <ConnectorList items={items} selected={selected} onSelect={setSelected} />
      </aside>
      <section className="overflow-auto p-6">
        {selected === "telegram" && <TelegramPanel />}
        {selected === "imessage" && <IMessagePanel />}
        {(selected === "gmail" || selected === "calendar") && <GooglePanel which={selected as "gmail" | "calendar"} />}
        {selected === "zapier" && <ZapierPanel />}
      </section>
    </div>
  );
}
