export interface FileInfo {
  available?: boolean;
  source_file_id?: string | null;
  id: string;
  name: string;
  size: number;
  created_at: string;
}
export interface FileList {
  items: FileInfo[];
  total: number;
}
export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
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
    throw new ApiError(
      typeof body?.detail === "string"
        ? body.detail
        : `请求失败（${response.status}）`,
      response.status,
    );
  }
  return response.json();
}
export const listFiles = (offset = 0) =>
  request<FileList>(`/api/files?limit=20&offset=${offset}`);
export function uploadFile(file: File, projectId?: string | null) {
  const body = new FormData();
  body.append("file", file);
  return request<FileInfo>(
    `/api/files${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ""}`,
    { method: "POST", body },
  );
}
export interface AssetInfo {
  id: string;
  name: string;
  mime: string;
  size: number;
  created_at: string;
}
export function uploadImage(file: File, projectId: string) {
  const body = new FormData();
  body.append("file", file);
  return request<AssetInfo>(
    `/api/projects/${encodeURIComponent(projectId)}/images`,
    { method: "POST", body },
  );
}
export const imageUrl = (id: string) =>
  `/api/images/${encodeURIComponent(id)}/download`;
export const downloadUrl = (id: string) =>
  `/api/files/${encodeURIComponent(id)}/download`;
export function formatSize(bytes: number) {
  return bytes < 1024 * 1024
    ? `${(bytes / 1024).toFixed(1)} KB`
    : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export interface DocumentBlock {
  id: string;
  position: number;
  kind: "heading" | "paragraph";
  level: number | null;
  text: string;
  style: string;
  editable: boolean;
  reason: string;
}
export interface DocumentContent {
  file: FileInfo;
  revision: string;
  blocks: DocumentBlock[];
  notice: string;
  table_count: number;
}
export const readDocument = (id: string) =>
  request<DocumentContent>(`/api/files/${encodeURIComponent(id)}/content`);
export const editDocument = (
  id: string,
  change: {
    block_id: string;
    revision: string;
    expected_text: string;
    new_text: string;
  },
) =>
  request<FileInfo>(`/api/files/${encodeURIComponent(id)}/edits`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(change),
  });

export const deleteFile = (id: string) =>
  request<{ id: string; deleted: boolean }>(
    `/api/files/${encodeURIComponent(id)}`,
    { method: "DELETE" },
  );

export interface WebSource {
  title: string;
  url: string;
  published_at?: string | null;
  retrieved_at: string;
  kind: "search" | "page";
}
export interface TaskChange {
  op: string;
  block_id: string;
  location: string;
  before: string;
  after: string;
}
export interface LocalEdit {
  path: string;
  name: string;
  source_file_id: string;
  new_file_id: string;
  source_sha256: string;
  dev?: number;
  ino?: number;
  overwritten?: { at: string; backup: string };
}
export interface ToolEvent {
  id: string;
  tool: string;
  label: string;
  status: "queued" | "running" | "ok" | "error" | "skipped";
  started_at: number;
  ended_at?: number;
  summary?: string;
  reason?: string;
  children?: ToolEvent[];
}
export interface TaskState {
  web_enabled?: boolean;
  web_activity?: string | null;
  activity?: string | null;
  repair_round?: number;
  creation_attempt?: number;
  id: string;
  project_id?: string;
  file: FileInfo | null;
  files?: FileInfo[];
  prompt: string;
  status:
    | "queued"
    | "running"
    | "cancelling"
    | "completed"
    | "failed"
    | "cancelled";
  stage: number;
  steps: string[];
  tools?: ToolEvent[];
  images?: AssetInfo[];
  elapsed_seconds: number;
  error: string | null;
  coverage?: string[];
  result: {
    web_sources?: WebSource[];
    web_status?: string;
    web_notices?: { code: string; message: string }[];
    outputs?: {
      file: FileInfo;
      changes: TaskChange[];
      kind?: "create" | "edit";
      renamed?: boolean;
    }[];
    kind?: "answer" | "edit" | "clarify" | "create";
    renamed?: boolean;
    renames?: { file_id: string; name: string }[];
    text?: string;
    file?: FileInfo;
    position?: number;
    summary?: string;
    explanation?: string;
    before?: string;
    after?: string;
    changes?: TaskChange[];
    citations?: {
      file_id: string;
      block_id: string;
      location: string;
      quote: string;
    }[];
    local_edit?: LocalEdit;
  } | null;
}
export const getTask = (id: string) =>
  request<TaskState>(`/api/tasks/${encodeURIComponent(id)}`);
export const cancelTask = (id: string) =>
  request<TaskState>(`/api/tasks/${encodeURIComponent(id)}/cancel`, {
    method: "POST",
  });
export const deleteTask = (id: string) =>
  request<{ id: string; deleted: boolean }>(
    `/api/tasks/${encodeURIComponent(id)}`,
    { method: "DELETE" },
  );
export interface OverwriteResult {
  path: string;
  backup: string;
  text: string;
}
export const overwriteLocalTask = (id: string) =>
  request<OverwriteResult>(
    `/api/tasks/${encodeURIComponent(id)}/overwrite-local`,
    { method: "POST" },
  );

export interface FileHistory {
  sources?: SourceFile[];
  history_id: string;
  items: TaskState[];
  total: number;
  next_cursor: string | null;
  current_file: FileInfo | null;
  active_task: TaskState | null;
}
export const getHistory = (id: string, before?: string) =>
  request<FileHistory>(
    `/api/files/${encodeURIComponent(id)}/history?limit=20${before ? `&before=${encodeURIComponent(before)}` : ""}`,
  );

export interface ProjectInfo {
  file_count: number;
  id: string;
  source_file_id: string | null;
  name: string;
  size: number | null;
  created_at: string | null;
  source_available: boolean;
}
export const listProjects = (offset = 0) =>
  request<{ items: ProjectInfo[]; total: number }>(
    `/api/projects?limit=20&offset=${offset}`,
  );
export const deleteProject = (id: string) =>
  request<{ id: string; deleted: boolean }>(
    `/api/projects/${encodeURIComponent(id)}`,
    { method: "DELETE" },
  );

export interface SourceFile extends FileInfo {
  origin?: "uploaded" | "generated";
  current_file: FileInfo | null;
}
export const createProject = () =>
  request<{ id: string; name: string }>("/api/projects", { method: "POST" });
export const renameProject = (id: string, name: string) =>
  request<{ id: string; name: string }>(
    `/api/projects/${encodeURIComponent(id)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    },
  );
export const projectFiles = (id: string) =>
  request<{ items: SourceFile[] }>(
    `/api/projects/${encodeURIComponent(id)}/files`,
  );
export const projectHistory = (id: string, before?: string) =>
  request<FileHistory>(
    `/api/projects/${encodeURIComponent(id)}/history?limit=20${before ? `&before=${encodeURIComponent(before)}` : ""}`,
  );
export const sendProjectTask = (
  project_id: string | null,
  file_ids: string[],
  prompt: string,
  web_enabled = true,
  image_ids: string[] = [],
) =>
  request<TaskState>("/api/tasks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project_id, file_ids, prompt, web_enabled, image_ids }),
  });

export const deleteSourceFile = (projectId: string, fileId: string) =>
  request<{ id: string; deleted: boolean }>(
    `/api/projects/${encodeURIComponent(projectId)}/files/${encodeURIComponent(fileId)}`,
    { method: "DELETE" },
  );
