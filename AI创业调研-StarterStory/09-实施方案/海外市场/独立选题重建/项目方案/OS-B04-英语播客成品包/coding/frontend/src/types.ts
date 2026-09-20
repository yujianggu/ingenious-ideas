export interface User {
  id: string;
  name: string;
  email: string;
  role: "editor" | "client";
  workspaceId: string;
}
export interface Clip {
  id: string;
  title: string;
  start: number;
  end: number;
  quote: string;
  context: string;
  post: string;
  checked: boolean;
  status: string;
  feedback: string;
}
export interface Asset {
  id: string;
  kind: string;
  clipId: string | null;
  version: number;
  filename: string;
  size: number;
  contentType: string;
  checked: boolean;
  createdAt: string;
}
export interface Episode {
  id: string;
  title: string;
  brand: string;
  source: string;
  duration: number;
  notes: string;
  glossary: string;
  prohibitedClaims: string;
  rights: boolean;
  clientId: string;
  phase: string;
  version: number;
  revision: number;
  createdAt: string;
  updatedAt: string;
  readyAt: string | null;
  dueAt: string | null;
  clips: Clip[];
  chapters: string;
  history: {
    version: number;
    sentAt: string;
    title: string;
    source: string;
    duration: number;
    chapters: string;
    clips: Clip[];
    decisions: {
      clipId: string;
      status: string;
      feedback: string;
      actorId: string;
      time: string;
    }[];
  }[];
  events: {
    id: string;
    action: string;
    actorId: string;
    actorName: string;
    time: string;
    version: number;
  }[];
  assets: Asset[];
}
