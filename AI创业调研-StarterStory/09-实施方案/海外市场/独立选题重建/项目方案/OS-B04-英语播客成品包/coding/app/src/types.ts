export type User = {
  id: string;
  name: string;
  email: string;
  role: "editor" | "client";
  workspaceId: string;
};
export type Clip = {
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
};
export type Asset = {
  id: string;
  kind: string;
  clipId: string | null;
  version: number;
  filename: string;
  size: number;
  checked: boolean;
  contentType: string;
};
export type Episode = {
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
  dueAt: string | null;
  readyAt: string | null;
  clips: Clip[];
  chapters: string;
  assets: Asset[];
  history: any[];
  events: {
    id: string;
    action: string;
    actorName: string;
    time: string;
    version: number;
  }[];
};
