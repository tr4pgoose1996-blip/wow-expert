import type { QuestItem } from "../types";

export function QuestTracker({ quests }: { quests: QuestItem[] }) {
  return (
    <div className="widget">
      <div className="widget-title">Quests</div>
      {quests.length === 0 ? (
        <div className="muted">No tracked quests.</div>
      ) : (
        quests.map((q) => (
          <div key={q.id} className="quest-row">
            <div className="quest-title">{q.title}</div>
            {q.objective && <div className="quest-obj">{q.objective}</div>}
            {q.progress && <div className="quest-progress">{q.progress}</div>}
            {q.zone && <div className="quest-zone">📍 {q.zone}</div>}
          </div>
        ))
      )}
    </div>
  );
}
