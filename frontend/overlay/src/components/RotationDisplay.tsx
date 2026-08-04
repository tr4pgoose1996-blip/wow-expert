import type { RotationDisplay } from "../types";

export function RotationDisplay({ data }: { data: RotationDisplay | null }) {
  if (!data) {
    return (
      <div className="widget">
        <div className="widget-title">Rotation</div>
        <div className="muted">No spec set. Teach Hermes your main spec.</div>
      </div>
    );
  }
  return (
    <div className="widget">
      <div className="widget-title">Rotation · {data.spec ?? "Unknown"}</div>
      <div className="rotation-hint">{data.priority_hint}</div>
      <div className="rotation-foot">
        Spec #{data.spec_id} · follow the advisor for the live priority list
      </div>
    </div>
  );
}
