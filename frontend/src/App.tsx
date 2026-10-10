import { useEffect, useState } from "react";
import { TopBar } from "./components/TopBar";
import { AIView } from "./features/anomalies/AIView";
import { ControlView } from "./features/control/ControlView";
import { AnalysisView } from "./features/replay/AnalysisView";
import { StatusView } from "./features/telemetry/StatusView";
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
  const [tab, setTabState] = useState<TabId>(() => {
    const h = location.hash.slice(1);
    return (TABS.find((t) => t.id === h)?.id ?? "control") as TabId;
  });
  const setTab = (t: TabId) => {
    setTabState(t);
    history.replaceState(null, "", `#${t}`);
  };
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
        {tab === "control" && <ControlView />}
        {tab === "status" && <StatusView />}
        {tab === "ai" && <AIView />}
        {tab === "analysis" && <AnalysisView />}
      </main>
    </div>
  );
}
