import type { CooldownItem } from "../types";

export function CooldownTracker({ items }: { items: CooldownItem[] }) {
  if (!items.length) {
    return (
      <div className="widget">
        <div className="widget-title">Cooldowns</div>
        <div className="muted">No active cooldowns tracked.</div>
      </div>
    );
  }
  const max = Math.max(...items.map((i) => i.priority), 1);
  return (
    <div className="widget">
      <div className="widget-title">Cooldowns</div>
      {items.map((c) => (
        <div key={c.name} className="cooldown-row">
          <div className="cooldown-name">{c.name}</div>
          <div className="cooldown-bar">
            <div
              className="cooldown-fill"
              style={{ width: `${(c.priority / max) * 100}%` }}
            />
          </div>
          <div className="cooldown-src">{c.source}</div>
        </div>
      ))}
    </div>
  );
}
