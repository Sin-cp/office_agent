import { computed, nextTick, onBeforeUnmount, ref } from 'vue';
import { getHistory, createTask, getTask, cancelTask, ApiError, type FileInfo, type TaskState } from './api';

const active = (task: TaskState) => ['queued', 'running', 'cancelling'].includes(task.status);
const storageKey = 'office-agent-selected-file';

export function useDocumentHistory(onComplete: () => void) {
  const projectId = ref<string | null>(null);
  const selected = ref<FileInfo | null>(null), records = ref<TaskState[]>([]);
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
  function isNearBottom() {
    const area = scrollArea.value;
    return !area || area.scrollHeight - area.scrollTop - area.clientHeight < 100;
  }
  function followBottom() {
    void nextTick(() => { const current = scrollArea.value; if (current) current.scrollTop = current.scrollHeight; });
  }
  function receive(value: TaskState) {
    const index = records.value.findIndex(item => item.id === value.id);
    if (index >= 0 && !active(records.value[index]!) && active(value)) return;
    const follow = isNearBottom();
    if (index < 0) records.value.push(value); else records.value[index] = value;
    if (follow) followBottom();
    if (!active(value)) {
      if (value.status === 'completed' && value.result && value.result.file.available !== false) {
        selected.value = value.result.file;
        requestedId.value = selected.value.id;
        remember(selected.value.id);
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
    selected.value = typeof file === 'string' ? null : file;
    requestedId.value = id;
    projectId.value = null;
    remember(id);
    records.value = []; known.value = false; cursor.value = null; olderLoading.value = false;
    historyLoading.value = true; historyError.value = ''; sendError.value = ''; progressError.value = ''; olderError.value = '';
    try {
      const result = await getHistory(id);
      if (disposed || revision !== epoch) return;
      projectId.value = result.history_id;
      selected.value = result.current_file;
      records.value = result.items;
      if (result.active_task && !records.value.some(item => item.id === result.active_task!.id)) records.value.push(result.active_task);
      cursor.value = result.next_cursor;
      known.value = true;
      if (selected.value) { requestedId.value = selected.value.id; remember(selected.value.id); }
      if (result.active_task) timer = setTimeout(() => void poll(result.active_task!.id, revision), 1000);
      void nextTick(() => { const area = scrollArea.value; if (area) area.scrollTop = area.scrollHeight; });
    } catch (cause) {
      if (disposed || revision !== epoch) return;
      historyError.value = cause instanceof ApiError && cause.status === 404 ? '文件或历史记录不存在，请重新选择文件。' : '历史记录加载失败，请重试。';
    } finally { if (revision === epoch) historyLoading.value = false; }
  }
  async function retry() {
    // A missing task needs an authoritative history refresh, not a guessed failure state.
    if (activeTask.value && progressError.value.includes('任务不存在')) records.value = records.value.filter(item => !active(item));
    if (requestedId.value) await select(selected.value || requestedId.value, true);
  }
  async function older() {
    if (!requestedId.value || !cursor.value || olderLoading.value) return;
    const revision = epoch;
    const area = scrollArea.value, height = area?.scrollHeight || 0, top = area?.scrollTop || 0;
    olderLoading.value = true; olderError.value = '';
    try {
      const result = await getHistory(requestedId.value, cursor.value);
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
    projectId.value = null; selected.value = null; requestedId.value = null; records.value = []; draft.value = ''; known.value = false;
    historyError.value = ''; progressError.value = ''; sendError.value = ''; olderError.value = ''; cursor.value = null;
    try { localStorage.removeItem(storageKey); localStorage.removeItem('office-agent-current-task'); } catch { /* optional */ }
  }
  async function send() {
    if (busy.value || historyLoading.value || historyError.value || !known.value || !selected.value || !draft.value.trim()) return;
    const revision = epoch;
    sending.value = true; sendError.value = ''; progressError.value = '';
    try {
      const value = await createTask(selected.value.id, draft.value.trim());
      if (disposed || revision !== epoch) return;
      receive(value); draft.value = '';
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
    let id: string | null = null, legacy: string | null = null;
    try { id = localStorage.getItem(storageKey); legacy = localStorage.getItem('office-agent-current-task'); } catch { /* optional */ }
    if (id) await select(id);
    else if (legacy) {
      historyLoading.value = true;
      try { const old = await getTask(legacy); await select(old.result?.file || old.file); }
      catch { historyError.value = '旧任务恢复失败，请从文件管理重新选择文件。'; }
      finally { historyLoading.value = false; }
    }
  }
  function projectDeleted(id: string) {
    if (projectId.value === id || requestedId.value === id) {
      clear();
      return true;
    }
    return false;
  }
  onBeforeUnmount(() => { disposed = true; ++epoch; clearTimeout(timer); });
  return { projectId, selected, records, draft, known, historyLoading, historyError, sending, stopping, progressError, sendError,
    olderLoading, olderError, cursor, busy, conversation, scrollArea, select, retry, older, clear, send, stop, restore, projectDeleted };
}
