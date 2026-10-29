<script setup lang="ts">
import { nextTick, onMounted, ref } from "vue";
import DocumentEditor from "./components/DocumentEditor.vue";
import Composer from "./components/Composer.vue";
import FileDrawer from "./components/FileDrawer.vue";
import { listFiles, uploadFile, type FileInfo } from "./api";
const files = ref<FileInfo[]>([]),
  total = ref(0),
  drawer = ref(false),
  loading = ref(false),
  uploading = ref(false);
const error = ref(""),
  message = ref(""),
  listError = ref("");
const drawerButton = ref<HTMLButtonElement>();
const activeFile = ref<FileInfo | null>(null);
const selectedFile = ref<FileInfo | null>(null);
const draft = ref("");
const composer = ref<InstanceType<typeof Composer>>();
async function selectFile(file: FileInfo) {
  drawer.value = false;
  activeFile.value = null;
  selectedFile.value = file;
  error.value = "";
  message.value = "";
  await nextTick();
  composer.value?.focus();
}
async function viewFile(file: FileInfo) {
  drawer.value = false;
  activeFile.value = file;
}
async function returnToComposer() {
  activeFile.value = null;
  await nextTick();
  composer.value?.focus();
}
function clearSelection() {
  selectedFile.value = null;
  message.value = "";
}
function fileDeleted(file: FileInfo) {
  files.value = files.value.filter(item => item.id !== file.id);
  total.value = Math.max(0, total.value - 1);
  if (selectedFile.value?.id === file.id) clearSelection();
  if (activeFile.value?.id === file.id) activeFile.value = null;
  message.value = `已删除：${file.name}`;
  void load();
}
let generation = 0;
async function load(more = false) {
  if (more && loading.value) return;
  const current = ++generation;
  loading.value = true;
  listError.value = "";
  try {
    const result = await listFiles(more ? files.value.length : 0);
    if (current !== generation) return;
    files.value = more
      ? [
          ...files.value,
          ...result.items.filter(
            (item) => !files.value.some((old) => old.id === item.id),
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
function openDrawer() {
  drawer.value = true;
  void load();
}
async function closeDrawer() {
  drawer.value = false;
  await nextTick();
  drawerButton.value?.focus();
}
async function upload(file: File) {
  if (uploading.value) return;
  uploading.value = true;
  error.value = "";
  message.value = `正在上传 ${file.name}…`;
  try {
    const result = await uploadFile(file);
    ++generation; // Ignore older list responses while publishing this successful upload.
    loading.value = false;
    files.value = [result, ...files.value];
    total.value++;
    message.value = `已上传：${result.name}`;
    await selectFile(result);
    message.value = `已上传：${result.name}`;
  } catch (cause) {
    error.value = (cause as Error).message;
    message.value = "";
  } finally {
    uploading.value = false;
  }
}
onMounted(() => void load());
</script>
<template>
  <div class="layout" :class="{ 'viewer-layout': activeFile }" :inert="drawer">
    <header>
      <a class="brand" href="/">Office<span>Agent</span></a>
      <div class="header-actions">
        <span class="version">v0.2.0</span
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
      <h1>让文档修改，<br /><span>像对话一样简单</span></h1>
      <Composer
        ref="composer"
        v-model="draft"
        :file="selectedFile"
        @clear="clearSelection"
        :busy="uploading"
        :message="message"
        :error="error"
        @upload="upload"
        @invalid="
          error = $event;
          message = '';
        "
      />
      </template>
      <DocumentEditor v-else :key="activeFile.id" :file="activeFile"
        @close="returnToComposer" />
    </main>
    <footer>AI Office Agent</footer>
  </div>
  <FileDrawer
    v-if="drawer"
    :files="files"
    :total="total"
    :loading="loading"
    :error="listError"
    @edit="selectFile"
    @view="viewFile"
    @deleted="fileDeleted"
    @close="closeDrawer"
    @more="load(true)"
    @retry="load()"
  />
</template>
