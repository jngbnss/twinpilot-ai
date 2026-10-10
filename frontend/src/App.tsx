import { useEffect, useState } from "react";
import { TopBar } from "./components/TopBar";
import { api } from "./services/api";
import { TwinSocket, wsUrl } from "./services/ws";
import { useTwin } from "./store/useTwin";

const TABS = [
  { id: "control", label: "조종" },
  { id: "status", label: "상태" },
  { id: "ai", label: "AI" },
  { id: "analysis", label: "분석 · 리플레이" },
] as const;

type TabId = (typeof TABS)[number]["id"];

export default function App() {
  const [tab, setTab] = useState<TabId>("control");
  const { ingest, setStatus, setLatency, setConfig } = useTwin.getState();

  useEffect(() => {
    api.config().then(setConfig).catch(() => undefined);
    const sock = new TwinSocket(wsUrl(), { onState: ingest, onStatus: setStatus, onLatency: setLatency });
    sock.connect();
    return () => sock.close();
  }, [ingest, setStatus, setLatency, setConfig]);

  return (
    <div className="app">
      <TopBar />
      <nav className="tabs">
        {TABS.map((t) => (
          <button key={t.id} className={tab === t.id ? "active" : ""} onClick={() => setTab(t.id)}>
            {t.label}
          </button>
        ))}
      </nav>
      <main className="content">
        <div className="panel">탭: {tab}</div>
      </main>
    </div>
  );
}
