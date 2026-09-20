export const TOKEN_KEY = "os-b01-token";
export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}
export async function response(
  path: string,
  options: RequestInit = {},
): Promise<Response> {
  const headers = new Headers(options.headers);
  const token = sessionStorage.getItem(TOKEN_KEY);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (options.body && !(options.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  let result: Response;
  try {
    result = await fetch(`/api${path}`, { ...options, headers });
  } catch {
    throw new ApiError(
      "Cannot reach the server. Your changes are still here. Try again.",
      0,
    );
  }
  if (!result.ok) {
    if (result.status === 401) {
      sessionStorage.removeItem(TOKEN_KEY);
      window.dispatchEvent(new Event("session-expired"));
    }
    const data = await result
      .json()
      .catch(() => ({ detail: `Request failed (${result.status})` }));
    const detail = Array.isArray(data.detail)
      ? data.detail
          .map(
            (x: { loc?: string[]; msg: string }) =>
              `${x.loc?.slice(1).join(".") || "Input"}: ${x.msg}`,
          )
          .join("; ")
      : data.detail;
    throw new ApiError(
      typeof detail === "string"
        ? detail
        : "The request could not be completed.",
      result.status,
    );
  }
  return result;
}
export async function request<T = unknown>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const r = await response(path, options);
  return r.status === 204 ? (undefined as T) : r.json();
}
export const json = (body: unknown, method = "POST"): RequestInit => ({
  method,
  body: JSON.stringify(body),
});
export async function download(path: string, name: string) {
  const r = await response(path);
  const url = URL.createObjectURL(await r.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
