import { Platform } from "react-native";
import * as DocumentPicker from "expo-document-picker";
import * as FileSystem from "expo-file-system/legacy";
import * as Sharing from "expo-sharing";
export async function pickFile(binary: boolean): Promise<any> {
  const result = await DocumentPicker.getDocumentAsync({
    copyToCacheDirectory: true,
    multiple: false,
  });
  if (result.canceled) return null;
  const limit = binary ? 500 * 1024 : 16 * 1024 * 1024;
  const message = binary
    ? "Choose a file smaller than 500 KiB."
    : "Choose a text import smaller than 16 MiB.";
  const file = result.assets[0];
  if (file.size !== undefined && file.size > limit) throw new Error(message);
  if (Platform.OS === "web") {
    const blob = file.file || (await (await fetch(file.uri)).blob());
    if (blob.size > limit) throw new Error(message);
    if (!binary) return blob.text();
    const data = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(",")[1]);
      reader.onerror = () => reject(new Error("Could not read file."));
      reader.readAsDataURL(blob);
    });
    return {
      name: file.name,
      contentType: file.mimeType || blob.type || "application/octet-stream",
      base64: data,
    };
  }
  const info = await FileSystem.getInfoAsync(file.uri);
  if (!info.exists || info.size > limit) throw new Error(message);
  const value = await FileSystem.readAsStringAsync(file.uri, {
    encoding: binary
      ? FileSystem.EncodingType.Base64
      : FileSystem.EncodingType.UTF8,
  });
  return binary
    ? {
        name: file.name,
        contentType: file.mimeType || "application/octet-stream",
        base64: value,
      }
    : value;
}
export async function openAttachment(file: {
  name: string;
  contentType: string;
  base64: string;
}) {
  if (Platform.OS === "web") {
    const raw = atob(file.base64);
    const bytes = Uint8Array.from(raw, (c) => c.charCodeAt(0));
    const url = URL.createObjectURL(
      new Blob([bytes], { type: file.contentType }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = file.name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    return;
  }
  if (!(await Sharing.isAvailableAsync()))
    throw new Error("Document sharing is not available on this device.");
  const path =
    FileSystem.cacheDirectory +
    "document-" +
    Date.now() +
    "-" +
    file.name.replace(/[^a-zA-Z0-9._-]/g, "_");
  await FileSystem.writeAsStringAsync(path, file.base64, {
    encoding: FileSystem.EncodingType.Base64,
  });
  try {
    await Sharing.shareAsync(path, { mimeType: file.contentType });
  } finally {
    await FileSystem.deleteAsync(path, { idempotent: true });
  }
}
