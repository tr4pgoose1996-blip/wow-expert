import { useEffect, useMemo, useRef, useState } from "react";
import { OverlayClient } from "./api/overlayClient";
import { BossAlerts } from "./components/BossAlerts";
import { CooldownTracker } from "./components/CooldownTracker";
import { QuestTracker } from "./components/QuestTracker";
import { RareAlerts } from "./components/RareAlerts";
import { RotationDisplay } from "./components/RotationDisplay";
import { WaypointDisplay } from "./components/WaypointDisplay";
import type {
  AlertItem,
  CooldownItem,
  OverlayFrame,
  QuestItem,
  RotationDisplay as RotationData,
  Waypoint,
} from "./types";

// In production these come from the auth flow; for the overlay demo they are
// read from the URL (?userId=&token=) or fallback to demo values.
function readAuth(): { userId: string; token: string } {
  const p = new URLSearchParams(window.location.search);
  return {
    userId: p.get("userId") ?? "00000000-0000-0000-0000-000000000000",
    token: p.get("token") ?? "demo-token",
  };
}

const MAX_ALERTS = 4;

export default function App() {
  const auth = useMemo(readAuth, []);
  const origin = useMemo(
    () => (window as unknown as { overlay?: { backendOrigin?: string } }).overlay?.backendOrigin ?? "http://localhost:8000",
    [],
  );

  const [connected, setConnected] = useState(false);
  const [rotation, setRotation] = useState<RotationData | null>(null);
  const [cooldowns, setCooldowns] = useState<CooldownItem[]>([]);
  const [quests, setQuests] = useState<QuestItem[]>([]);
  const [bosses, setBosses] = useState<AlertItem[]>([]);
  const [rares, setRares] = useState<AlertItem[]>([]);
  const [waypoint, setWaypoint] = useState<Waypoint | null>(null);

  const clientRef = useRef<OverlayClient | null>(null);

  useEffect(() => {
    const client = new OverlayClient({
      origin,
      userId: auth.userId,
      token: auth.token,
      onStatus: setConnected,
      onFrame: (frame: OverlayFrame) => {
        const ts = Date.now();
        switch (frame.type) {
          case "hello":
            setRotation((frame.rotation as RotationData) ?? null);
            setCooldowns((frame.cooldowns as CooldownItem[]) ?? []);
            setQuests((frame.quests as QuestItem[]) ?? []);
            break;
          case "rotation":
            setRotation(frame as unknown as RotationData);
            break;
          case "cooldowns":
            setCooldowns((frame.cooldowns as CooldownItem[]) ?? []);
            break;
          case "quests":
            setQuests((frame.quests as QuestItem[]) ?? []);
            break;
          case "boss_alert":
            setBosses((prev) => [{ ...(frame as unknown as AlertItem), timestamp: ts }, ...prev].slice(0, MAX_ALERTS));
            break;
          case "rare_alert":
            setRares((prev) => [{ ...(frame as unknown as AlertItem), timestamp: ts }, ...prev].slice(0, MAX_ALERTS));
            break;
          case "waypoint":
            setWaypoint(frame as unknown as Waypoint);
            break;
          default:
            break;
        }
      },
    });
    clientRef.current = client;
    client.connect();
    return () => client.close();
  }, [auth.userId, auth.token, origin]);

  return (
    <div className="overlay-root">
      <div className="status-bar">
        <span className={`dot ${connected ? "on" : "off"}`} />
        <span className="status-text">Hermes {connected ? "live" : "offline"}</span>
      </div>
      <RotationDisplay data={rotation} />
      <CooldownTracker items={cooldowns} />
      <QuestTracker quests={quests} />
      <BossAlerts alerts={bosses} />
      <RareAlerts alerts={rares} />
      <WaypointDisplay waypoint={waypoint} />
    </div>
  );
}
