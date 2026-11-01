<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue';
import TaskProgress from './components/TaskProgress.vue';
import DocumentEditor from './components/DocumentEditor.vue';
import Composer from './components/Composer.vue';
import FileDrawer from './components/FileDrawer.vue';
import { listProjects, uploadFile, type FileInfo, type ProjectInfo } from './api';
import { useDocumentHistory } from './useDocumentHistory';
const projects = ref<ProjectInfo[]>([]), total = ref(0), drawer = ref(false), loading = ref(false), uploading = ref(false);
const error = ref(''), message = ref(''), listError = ref('');
const drawerButton = ref<HTMLButtonElement>();
const activeFile = ref<FileInfo | null>(null);
const previewProjectId = ref<string | null>(null);
const composer = ref<InstanceType<typeof Composer>>();
const { projectId, selected: selectedFile, records, draft, known, historyLoading, historyError, stopping, progressError, sendError,
  olderLoading, olderError, cursor, busy: taskBusy, conversation, scrollArea, select, retry, older, clear, send, stop, restore, projectDeleted } = useDocumentHistory(() => void load());
async function selectProject(project: ProjectInfo) {
  if (taskBusy.value || uploading.value) return;
  drawer.value = false; activeFile.value = null; error.value = ''; message.value = '';
  await select(project.id, !selectedFile.value);
  await nextTick(); composer.value?.focus();
}
function clearSelection() {
  if (taskBusy.value || uploading.value || historyLoading.value) return;
  clear(); activeFile.value = null; error.value = ''; message.value = '';
}
function viewFile(file: FileInfo) { drawer.value = false; activeFile.value = file; previewProjectId.value = projectId.value; }
function viewProject(project: ProjectInfo) {
  if (!project.source_available || !project.source_file_id) return;
  drawer.value = false;
  previewProjectId.value = project.id;
  activeFile.value = { id: project.source_file_id, name: project.name, size: project.size ?? 0, created_at: project.created_at ?? '' };
}
async function returnToComposer() { activeFile.value = null; await nextTick(); composer.value?.focus(); }
function projectRemoved(project: ProjectInfo) {
  const cleared = projectDeleted(project.id);
  if (previewProjectId.value === project.id || cleared) activeFile.value = null;
  if (cleared) { error.value = ''; message.value = ''; }
  projects.value = projects.value.filter(item => item.id !== project.id);
  total.value = Math.max(0, total.value - 1);
  void load();
}
let generation = 0;
async function load(more = false) {
  if (more && loading.value) return;
  const current = ++generation;
  loading.value = true;
  listError.value = "";
  try {
    const result = await listProjects(more ? projects.value.length : 0);
    if (current !== generation) return;
    projects.value = more
      ? [
          ...projects.value,
          ...result.items.filter(
            (item) => !projects.value.some((old) => old.id === item.id),
          ),
        ]
      : result.items;
    total.value = result.total;
  } catch (cause) {
    if (current === generation) listError.value = (cause as Error).message;
  } finally {
    if (current === generation) loading.value = false;
  }
}

function openDrawer() { drawer.value = true; void load(); }
async function closeDrawer() { drawer.value = false; await nextTick(); drawerButton.value?.focus(); }
async function upload(file: File) {
  if (uploading.value || taskBusy.value || historyLoading.value) return;
  uploading.value = true; error.value = ''; message.value = `正在上传 ${file.name}…`;
  try {
    const result = await uploadFile(file);
    void load();
    activeFile.value = null;
    await select(result, !selectedFile.value);
    message.value = '';
  } catch (cause) { error.value = (cause as Error).message; message.value = ''; }
  finally { uploading.value = false; }
}
onMounted(() => { void load(); void restore(); });
</script>

<template>
  <div class="layout" :class="{ 'viewer-layout': activeFile, 'conversation-layout': conversation && !activeFile }" :inert="drawer">
    <header>
      <a class="brand" href="/">Office<span>Agent</span></a>
      <div class="header-actions">
        <span class="version">v0.3.0</span
        ><button
          ref="drawerButton"
          class="files-toggle"
          aria-label="打开已上传文件"
          aria-haspopup="dialog"
          :aria-expanded="drawer"
          @click="openDrawer"
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="1.7"
            aria-hidden="true"
          >
            <rect x="3" y="4" width="18" height="16" rx="3" />
            <path d="M15 4v16" /></svg
          ><span>{{ total }}</span>
        </button>
      </div>
    </header>
    <main>
      <template v-if="!activeFile">
      <div v-if="conversation" ref="scrollArea" class="conversation-history" aria-label="文件修改历史">
        <button v-if="cursor" class="text-button older-messages" :disabled="olderLoading" @click="older">{{ olderLoading ? '正在加载…' : '加载更早记录' }}</button>
        <p v-if="olderError" role="alert" class="feedback error">{{ olderError }}</p>
        <article v-for="record in records" :key="record.id" class="conversation-turn">
          <div class="user-message"><p>{{ record.prompt }}</p></div>
          <TaskProgress :task="record" :stopping="stopping" @stop="stop" @view="viewFile" />
        </article>
      </div>
      <h1 v-else-if="!historyLoading && !historyError && (!selectedFile || known)">让文档修改，<br /><span>像对话一样简单</span></h1>
      <div class="composer-dock">
        <p v-if="conversation && !selectedFile" class="history-status">此历史已没有可用文件。<button class="text-button" @click="clearSelection">返回首页</button></p>
        <p v-if="historyLoading" role="status" class="history-status">正在加载文件历史…</p>
        <div v-if="historyError" class="history-status"><p class="feedback error" role="alert">{{ historyError }}</p><button class="text-button" @click="retry">重新加载历史</button></div>
        <div v-if="progressError" class="history-status"><p class="feedback error" role="alert">{{ progressError }}</p><button v-if="progressError.includes('任务不存在')" class="text-button" @click="retry">重新加载历史</button></div>
        <Composer ref="composer" v-model="draft" :file="selectedFile" :conversation="conversation"
          :busy="uploading || taskBusy || historyLoading" :send-disabled="!known || !!historyError"
          :message="message" :error="error || sendError" @clear="clearSelection" @send="send"
          @upload="upload" @invalid="error = $event; message = ''" />
      </div>
      </template>
      <DocumentEditor v-else :key="activeFile.id" :file="activeFile"
        @close="returnToComposer" />
    </main>
    <footer>AI Office Agent</footer>
  </div>
  <FileDrawer
    v-if="drawer"
    :projects="projects"
    :busy="taskBusy || uploading || historyLoading"
    :total="total"
    :loading="loading"
    :error="listError"
    @edit="selectProject"
    @view="viewProject"
    @deleted="projectRemoved"
    @close="closeDrawer"
    @more="load(true)"
    @retry="load()"
  />
</template>
