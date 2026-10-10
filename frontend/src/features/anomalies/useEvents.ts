import { useEffect, useState } from "react";
import { api } from "../../services/api";
import { useTwin } from "../../store/useTwin";
import type { TimelineEvent } from "../../types/api";

/** Polls the active run's timeline (faults, safety, anomaly events) every 2 s. */
export function useEvents(): TimelineEvent[] {
  const runId = useTwin((s) => s.state.run_id);
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  useEffect(() => {
    setEvents([]);
    if (!runId) return;
    let alive = true;
    const load = () =>
      api
        .events(0)
        .then((ev) => alive && setEvents(ev))
        .catch(() => undefined);
    load();
    const id = setInterval(load, 2000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [runId]);
  return events;
}
