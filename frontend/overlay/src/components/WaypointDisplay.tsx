import type { Waypoint } from "../types";

export function WaypointDisplay({ waypoint }: { waypoint: Waypoint | null }) {
  if (!waypoint) {
    return (
      <div className="widget">
        <div className="widget-title">Waypoint</div>
        <div className="muted">No active waypoint.</div>
      </div>
    );
  }
  return (
    <div className="widget waypoint">
      <div className="widget-title">Waypoint</div>
      <div className="wp-label">{waypoint.label}</div>
      {waypoint.note && <div className="wp-note">{waypoint.note}</div>}
      <div className="wp-pin">
        map: {(waypoint.pin.x * 100).toFixed(0)}%, {(waypoint.pin.y * 100).toFixed(0)}%
      </div>
    </div>
  );
}
