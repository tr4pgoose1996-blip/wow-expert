import type { AlertItem } from "../types";

export function RareAlerts({ alerts }: { alerts: AlertItem[] }) {
  return (
    <div className="widget alert-zone">
      <div className="widget-title">Rare Spawns</div>
      {alerts.length === 0 ? (
        <div className="muted">No rare spawns reported.</div>
      ) : (
        alerts.map((a, i) => (
          <div key={i} className="alert rare">
            <div className="alert-head">{a.name ?? "Rare"}</div>
            {a.zone && <div className="alert-zone-name">📍 {a.zone}</div>}
            {a.pin && (
              <div className="alert-pin">
                map: {(a.pin.x * 100).toFixed(0)}%, {(a.pin.y * 100).toFixed(0)}%
              </div>
            )}
          </div>
        ))
      )}
    </div>
  );
}
