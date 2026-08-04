// Shared types for the overlay frame contract (mirrors app/modules/realtime/domain.py).

export type FrameType =
  | "hello"
  | "rotation"
  | "cooldowns"
  | "quests"
  | "boss_alert"
  | "rare_alert"
  | "waypoint"
  | "error";

export interface OverlayFrame {
  type: FrameType;
  seq: number;
  [key: string]: unknown;
}

export interface RotationDisplay {
  spec_id: number;
  spec: string | null;
  priority_hint: string;
}

export interface CooldownItem {
  name: string;
  source: string;
  priority: number;
}

export interface QuestItem {
  id: string;
  title: string;
  objective: string;
  progress?: string;
  zone?: string;
}

export interface AlertItem {
  boss?: string;
  name?: string;
  mechanic?: string;
  phase?: string;
  severity?: string;
  zone?: string;
  pin?: { x: number; y: number };
  note?: string;
  timestamp: number;
}

export interface Waypoint {
  label: string;
  pin: { x: number; y: number };
  note?: string;
}
