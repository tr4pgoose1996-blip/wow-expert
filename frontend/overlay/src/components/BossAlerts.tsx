import type { AlertItem } from "../types";

export function BossAlerts({ alerts }: { alerts: AlertItem[] }) {
  return (
    <div className="widget alert-zone">
      <div className="widget-title">Boss Alerts</div>
      {alerts.length === 0 ? (
        <div className="muted">No active boss alerts.</div>
      ) : (
        alerts.map((a, i) => (
          <div key={i} className={`alert boss sev-${a.severity ?? "high"}`}>
            <div className="alert-head">{a.boss ?? "Boss"}</div>
            {a.phase && <div className="alert-phase">Phase: {a.phase}</div>}
            {a.mechanic && <div className="alert-mech">{a.mechanic}</div>}
          </div>
        ))
      )}
    </div>
  );
}
