export interface FileInfo {
  id: string;
  name: string;
  size: number;
  created_at: string;
}
export interface FileList {
  items: FileInfo[];
  total: number;
}
async function request<T>(url: string, options?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, {
      ...options,
      signal: AbortSignal.timeout(60000),
    });
  } catch {
    throw new Error(
      "请求失败或超时，请检查后端连接；上传超时后请先刷新文件列表确认结果。",
    );
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(
      typeof body?.detail === "string"
        ? body.detail
        : `请求失败（${response.status}）`,
    );
  }
  return response.json();
}
export const listFiles = (offset = 0) =>
  request<FileList>(`/api/files?limit=20&offset=${offset}`);
export function uploadFile(file: File) {
  const body = new FormData();
  body.append("file", file);
  return request<FileInfo>("/api/files", { method: "POST", body });
}
export const downloadUrl = (id: string) =>
  `/api/files/${encodeURIComponent(id)}/download`;
export function formatSize(bytes: number) {
  return bytes < 1024 * 1024
    ? `${(bytes / 1024).toFixed(1)} KB`
    : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
