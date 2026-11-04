import { computed, nextTick, onBeforeUnmount, ref } from 'vue';
import { getHistory, projectHistory, sendProjectTask, createProject, getTask, cancelTask, ApiError, type FileInfo, type TaskState } from './api';

const active = (task: TaskState) => ['queued', 'running', 'cancelling'].includes(task.status);
const storageKey = 'office-agent-selected-project';

export function useDocumentHistory(onComplete: () => void) {
  const webEnabled = ref(true);
  try { webEnabled.value = localStorage.getItem('office-agent-web-enabled') !== 'false'; } catch { /* optional */ }
  function setWebEnabled(value: boolean) {
    if (busy.value) return;
    webEnabled.value = value;
    try { localStorage.setItem('office-agent-web-enabled', String(value)); } catch { /* optional */ }
  }
  const projectId = ref<string | null>(null);
  let sourceRoots: Record<string, string> = {};
  const sourceCount = ref(0);
  const attachments = ref<FileInfo[]>([]);
  const selected = computed(() => attachments.value[0] || null);
  const records = ref<TaskState[]>([]);
  const draft = ref(''), known = ref(false), historyLoading = ref(false), historyError = ref('');
  const sending = ref(false), stopping = ref(false), progressError = ref(''), sendError = ref('');
  const olderLoading = ref(false), olderError = ref(''), cursor = ref<string | null>(null);
  const requestedId = ref<string | null>(null);
  const activeTask = computed(() => records.value.find(active));
  const busy = computed(() => sending.value || !!activeTask.value);
  const conversation = computed(() => known.value && records.value.length > 0);
  const scrollArea = ref<HTMLElement>();
  let epoch = 0, timer: ReturnType<typeof setTimeout> | undefined, disposed = false;
  function remember(id: string) { try { localStorage.setItem(storageKey, id); localStorage.removeItem('office-agent-current-task'); } catch { /* optional */ } }
  function rememberAttachments() {
    if (!projectId.value) return;
    try { localStorage.setItem(`office-agent-attachments:${projectId.value}`, JSON.stringify(attachments.value.map(f => sourceRoots[f.id] || f.id))); } catch { /* optional */ }
  }
  function isNearBottom() {
    const area = scrollArea.value;
    return !area || area.scrollHeight - area.scrollTop - area.clientHeight < 100;
  }
  function followBottom() {
    void nextTick(() => { const current = scrollArea.value; if (current) current.scrollTop = current.scrollHeight; });
  }
  function receive(value: TaskState, scrollToLatest = false) {
    const index = records.value.findIndex(item => item.id === value.id);
    const previous = index >= 0 ? records.value[index] : undefined;
    if (index >= 0 && !active(records.value[index]!) && active(value)) return;
    const follow = scrollToLatest || isNearBottom();
    if (index < 0) records.value.push(value); else records.value[index] = value;
    if (follow) followBottom();
    if (!active(value)) {
      if (value.status === 'completed' && (index < 0 || active(previous!))) {
        const outputs = value.result?.outputs || (value.result?.file ? [{file:value.result.file}] : []);
        for (const output of outputs) {
          sourceRoots[output.file.id] = sourceRoots[output.file.source_file_id || ''] || output.file.source_file_id || output.file.id;
          if (value.result?.kind === 'create') {
            if (!attachments.value.some(f => f.id === output.file.id)) attachments.value.push(output.file);
            sourceCount.value++;
          }
          attachments.value = attachments.value.map(f => f.id === output.file.source_file_id ? output.file : f);
        }
        rememberAttachments();
        onComplete();
      }
    }
  }
  async function poll(id: string, revision: number) {
    try {
      const value = await getTask(id);
      if (disposed || revision !== epoch) return;
      const existing = records.value.find(item => item.id === id);
      if (existing && !active(existing)) return;
      receive(value);
      progressError.value = '';
    } catch (cause) {
      if (disposed || revision !== epoch) return;
      if (cause instanceof ApiError && cause.status === 404) {
        progressError.value = '任务不存在，请重新加载历史确认状态。';
        return;
      }
      progressError.value = '暂时无法获取进度，正在重新连接；后台任务可能仍在执行。';
    }
    if (!disposed && revision === epoch && activeTask.value) timer = setTimeout(() => void poll(id, revision), 1000);
  }
  async function select(file: FileInfo | string, preserveDraft = false) {
    if (busy.value) return;
    const revision = ++epoch;
    clearTimeout(timer);
    const id = typeof file === 'string' ? file : file.id;
    if (!preserveDraft && selected.value?.id !== id) draft.value = '';
    attachments.value = []; sourceRoots = {}; sourceCount.value = 0;
    requestedId.value = id;
    projectId.value = null;
    remember(id);
    records.value = []; known.value = false; cursor.value = null; olderLoading.value = false;
    historyLoading.value = true; historyError.value = ''; sendError.value = ''; progressError.value = ''; olderError.value = '';
    try {
      const result = await projectHistory(id);
      if (disposed || revision !== epoch) return;
      projectId.value = result.history_id;
      sourceCount.value = result.sources?.length || 0;
      const sources = result.sources || [];
      for (const source of sources) if (source.current_file) sourceRoots[source.current_file.id] = source.id;
      let chosen: string[] | null = null;
      try {
        const saved = JSON.parse(localStorage.getItem(`office-agent-attachments:${result.history_id}`) || 'null');
        if (Array.isArray(saved)) chosen = saved;
      } catch { /* use all attachments for legacy sessions */ }
      attachments.value = sources.flatMap(s => s.current_file?.available !== false && s.current_file && (chosen === null || chosen.includes(s.id)) ? [s.current_file] : []);

      records.value = result.items;
      if (result.active_task && !records.value.some(item => item.id === result.active_task!.id)) records.value.push(result.active_task);
      cursor.value = result.next_cursor;
      known.value = true;
      requestedId.value = result.history_id; remember(result.history_id);
      if (result.active_task) timer = setTimeout(() => void poll(result.active_task!.id, revision), 1000);
      void nextTick(() => { const area = scrollArea.value; if (area) area.scrollTop = area.scrollHeight; });
    } catch (cause) {
      if (disposed || revision !== epoch) return;
      historyError.value = cause instanceof ApiError && cause.status === 404 ? '项目或历史记录不存在，请重新选择项目。' : '历史记录加载失败，请重试。';
    } finally { if (revision === epoch) historyLoading.value = false; }
  }
  async function retry() {
    // A missing task needs an authoritative history refresh, not a guessed failure state.
    if (activeTask.value && progressError.value.includes('任务不存在')) records.value = records.value.filter(item => !active(item));
    if (requestedId.value) await select(requestedId.value, true);
  }
  async function older() {
    if (!requestedId.value || !cursor.value || olderLoading.value) return;
    const revision = epoch;
    const area = scrollArea.value, height = area?.scrollHeight || 0, top = area?.scrollTop || 0;
    olderLoading.value = true; olderError.value = '';
    try {
      const result = await projectHistory(requestedId.value, cursor.value);
      if (revision !== epoch || disposed) return;
      const ids = new Set(records.value.map(item => item.id));
      records.value = [...result.items.filter(item => !ids.has(item.id)), ...records.value];
      cursor.value = result.next_cursor;
      await nextTick();
      if (area) area.scrollTop = top + area.scrollHeight - height;
    } catch { if (revision === epoch) olderError.value = '更早记录加载失败，请重试。'; }
    finally { if (revision === epoch) olderLoading.value = false; }
  }
  function clear() {
    if (busy.value || historyLoading.value) return;
    ++epoch; clearTimeout(timer);
    sourceRoots = {};
    projectId.value = null; sourceCount.value = 0; attachments.value = []; requestedId.value = null; records.value = []; draft.value = ''; known.value = false;
    historyError.value = ''; progressError.value = ''; sendError.value = ''; olderError.value = ''; cursor.value = null;
    try { localStorage.removeItem(storageKey); localStorage.removeItem('office-agent-current-task'); } catch { /* optional */ }
  }
  async function send() {
    if (busy.value || historyLoading.value || historyError.value || !draft.value.trim()) return;
    const revision = epoch;
    sending.value = true; sendError.value = ''; progressError.value = '';
    try {
      const value = await sendProjectTask(projectId.value, attachments.value.map(f => f.id), draft.value.trim(), webEnabled.value);
      if (disposed || revision !== epoch) return;
      projectId.value = value.project_id!; requestedId.value = value.project_id!; remember(value.project_id!); known.value = true;
      receive(value, true); draft.value = ''; onComplete();
      if (active(value)) timer = setTimeout(() => void poll(value.id, revision), 1000);
    } catch (cause) { if (revision === epoch) sendError.value = (cause as Error).message; }
    finally { sending.value = false; }
  }
  async function stop() {
    if (!activeTask.value || stopping.value) return;
    const revision = epoch, id = activeTask.value.id;
    stopping.value = true;
    try {
      const value = await cancelTask(id);
      if (revision !== epoch || disposed) return;
      receive(value); progressError.value = '';
    } catch { if (revision === epoch) progressError.value = '停止请求未确认，请重试；后台任务可能仍在执行。'; }
    finally { stopping.value = false; }
  }
  async function restore() {
    let id: string | null = null;
    try {
      id = localStorage.getItem(storageKey);
      if (!id) {
        const old = localStorage.getItem('office-agent-selected-file');
        if (old) {
          const history = await getHistory(old);
          id = history.history_id;
          localStorage.removeItem('office-agent-selected-file');
        }
      }
    } catch { historyError.value = '恢复会话失败，请从项目管理重新选择。'; }
    if (id) await select(id);
  }
  async function ensureProject() {
    if (!projectId.value) {
      const project = await createProject();
      projectId.value = project.id; requestedId.value = project.id; remember(project.id); known.value = true;
    }
    return projectId.value;
  }
  function attach(file: FileInfo) {
    if (busy.value) return;
    if (!attachments.value.some(f => f.id === file.id)) attachments.value.push(file);
    rememberAttachments();
  }
  function detach(id: string) {
    if (!busy.value) { attachments.value = attachments.value.filter(f => f.id !== id); rememberAttachments(); }
  }
  function projectDeleted(id: string) {
    try { localStorage.removeItem(`office-agent-attachments:${id}`); } catch { /* optional */ }
    if (projectId.value === id || requestedId.value === id) {
      clear();
      return true;
    }
    return false;
  }
  onBeforeUnmount(() => { disposed = true; ++epoch; clearTimeout(timer); });
  return { webEnabled, setWebEnabled, projectId, selected, sourceCount, attachments, ensureProject, attach, detach, records, draft, known, historyLoading, historyError, sending, stopping, progressError, sendError,
    olderLoading, olderError, cursor, busy, conversation, scrollArea, select, retry, older, clear, send, stop, restore, projectDeleted };
}
