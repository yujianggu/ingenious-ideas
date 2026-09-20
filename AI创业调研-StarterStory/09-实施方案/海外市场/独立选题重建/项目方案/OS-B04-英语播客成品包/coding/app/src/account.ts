export type AccountData = {
  episode: unknown;
  episodes: unknown[];
  members: unknown[];
  invite: string;
  inviteResult: string;
  creating: boolean;
};
export function accountBoundary(
  _previous: AccountData,
  _event: "expired" | "authenticated" | "signed-out" | "connection-changed",
): {
  episode: null;
  episodes: never[];
  members: never[];
  invite: string;
  inviteResult: string;
  creating: boolean;
} {
  return {
    episode: null,
    episodes: [],
    members: [],
    invite: "",
    inviteResult: "",
    creating: false,
  };
}
