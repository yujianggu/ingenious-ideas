export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}
export function normalizeError(body: any): string {
  if (typeof body?.detail === "string") return body.detail;
  if (Array.isArray(body?.detail))
    return body.detail
      .map((x: any) => `${x.loc?.slice(1).join(".") || "Input"}: ${x.msg}`)
      .join("\n");
  return "Request failed. Please try again.";
}
export function decisionPayload(
  revision: number,
  version: number,
  clipId: string,
  action: string,
  read: boolean,
  feedback: string,
): {
  revision: number;
  version: number;
  clipId: string;
  read?: boolean;
  feedback?: string;
} {
  if (action === "approve" && !read)
    throw new Error("Please read the complete clip before approving.");
  if (action === "changes" && !feedback.trim())
    throw new Error("Please add specific feedback.");
  return {
    revision,
    version,
    clipId,
    ...(action === "approve" ? { read: true } : { feedback: feedback.trim() }),
  };
}
export async function request<T = any>(
  base: string,
  token: string | null,
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const multipart = typeof FormData !== "undefined" && body instanceof FormData;
  let res: Response;
  try {
    res = await fetch(base.replace(/\/$/, "") + path, {
      method,
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(body && !multipart ? { "Content-Type": "application/json" } : {}),
      },
      body: body
        ? multipart
          ? (body as FormData)
          : JSON.stringify(body)
        : undefined,
    });
  } catch {
    throw new ApiError(
      0,
      "Cannot reach your studio. Check the API address and network, then retry.",
    );
  }
  if (!res.ok) {
    let data;
    try {
      data = await res.json();
    } catch {
      data = null;
    }
    throw new ApiError(res.status, normalizeError(data));
  }
  return res.status === 204 ? (undefined as T) : res.json();
}
