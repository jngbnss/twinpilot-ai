export function SimBadge({ quality = "simulated" }: { quality?: string }) {
  const label = quality === "replayed" ? "REPLAYED · SIMULATED SENSOR" : "SIMULATED SENSOR";
  return (
    <span className="badge-sim" title="실제 장비 센서가 아닌 시뮬레이션 값">
      {label}
    </span>
  );
}
