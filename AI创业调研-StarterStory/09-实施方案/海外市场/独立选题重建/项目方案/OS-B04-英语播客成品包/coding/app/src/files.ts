import { Platform } from "react-native";
import * as FileSystem from "expo-file-system/legacy";
import * as Sharing from "expo-sharing";
import * as DocumentPicker from "expo-document-picker";
import { ApiError, request } from "./api";
import type { Episode } from "./types";
export async function upload(
  base: string,
  token: string,
  episode: Episode,
  kind: string,
  clipId: string,
) {
  const result = await DocumentPicker.getDocumentAsync({
    copyToCacheDirectory: true,
    multiple: false,
  });
  if (result.canceled) return null;
  const asset = result.assets[0];
  const data = new FormData();
  data.append("revision", String(episode.revision));
  data.append("kind", kind);
  if (kind === "video" || kind === "subtitle") data.append("clipId", clipId);
  if (Platform.OS === "web") {
    data.append(
      "file",
      asset.file || (await (await fetch(asset.uri)).blob()),
      asset.name,
    );
  } else {
    data.append("file", {
      uri: asset.uri,
      name: asset.name,
      type: asset.mimeType || "application/octet-stream",
    } as any);
  }
  return request<Episode>(
    base,
    token,
    `/episodes/${episode.id}/assets`,
    "POST",
    data,
  );
}
export async function download(
  base: string,
  token: string,
  path: string,
  filename: string,
) {
  const url = base.replace(/\/$/, "") + path;
  if (Platform.OS === "web") {
    const res = await fetch(url, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok)
      throw new ApiError(res.status, "Download failed. Refresh and try again.");
    const blob = await res.blob();
    const objectURL = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = objectURL;
    a.download = filename;
    a.click();
    setTimeout(() => URL.revokeObjectURL(objectURL), 1000);
    return;
  }
  const directory = FileSystem.cacheDirectory + "episode-downloads/";
  await FileSystem.makeDirectoryAsync(directory, { intermediates: true });
  const target =
    directory + Date.now() + "-" + filename.replace(/[^a-zA-Z0-9._-]/g, "_");
  const result = await FileSystem.downloadAsync(url, target, {
    headers: { Authorization: `Bearer ${token}` },
  });
  try {
    if (result.status !== 200)
      throw new ApiError(
        result.status,
        "Download failed. Refresh and try again.",
      );
    if (!(await Sharing.isAvailableAsync()))
      throw new Error("File sharing is unavailable on this device.");
    await Sharing.shareAsync(result.uri);
  } finally {
    await FileSystem.deleteAsync(target, { idempotent: true });
  }
}
