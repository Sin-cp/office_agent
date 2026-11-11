<template>
  <el-config-provider :locale="zhCn">
    <div
      class="app-shell"
      :class="{ 'sidebar-collapsed': !sidebarOpen }"
      :inert="!!sourceProjectId"
    >
      <div
        class="sidebar-shell"
        :class="{ 'is-open': sidebarOpen }"
        :inert="!sidebarOpen"
        :aria-hidden="!sidebarOpen"
      >
        <ProjectSidebar
          :projects="projects"
          :total="total"
          :active-project-id="selectingProjectId || projectId"
          :busy="taskBusy || uploading"
          :switching="historyLoading"
          :loading="loading"
          :error="listError"
          @new-session="newSession"
          @collapse="toggleSidebar"
          @select="selectProject"
          @view="viewProject"
          @deleted="projectRemoved"
          @renamed="load()"
          @more="load(true)"
          @retry="load(false, true)"
        />
      </div>
      <Transition name="fade">
        <div
          v-if="sidebarOpen"
          class="sidebar-backdrop"
          aria-hidden="true"
          @click="toggleSidebar"
        ></div>
      </Transition>

      <section
        class="workspace"
        :class="{
          'viewer-layout': activeFile,
          'conversation-layout': conversation && !activeFile,
        }"
      >
        <header
          class="workspace-bar"
          :class="{ 'is-visible': !sidebarOpen }"
          :inert="sidebarOpen"
          :aria-hidden="sidebarOpen"
        >
          <template v-if="!sidebarOpen">
            <el-tooltip
              content="展开项目面板"
              placement="bottom"
              :show-after="400"
            >
              <el-button
                class="chrome-button panel-toggle"
                text
                circle
                aria-label="展开项目面板"
                data-sidebar-toggle
                @click="toggleSidebar"
              >
                <svg
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="1.7"
                  aria-hidden="true"
                >
                  <rect x="3.4" y="4.4" width="17.2" height="15.2" rx="3.2" />
                  <path d="M9.2 4.6v14.8" />
                </svg>
              </el-button>
            </el-tooltip>
            <el-tooltip content="新会话" placement="bottom" :show-after="400">
              <el-button
                class="chrome-button"
                text
                circle
                aria-label="新会话"
                :disabled="taskBusy || uploading || historyLoading"
                @click="newSession"
              >
                <svg
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="1.8"
                  aria-hidden="true"
                >
                  <path d="M12 5v14M5 12h14" stroke-linecap="round" />
                </svg>
              </el-button>
            </el-tooltip>
            <span v-if="conversation" class="workspace-bar-title">{{
              currentProjectName
            }}</span>
          </template>
        </header>

        <main
          :class="
            activeFile
              ? 'viewer-main'
              : conversation
                ? 'conversation-main'
                : 'home-main'
          "
        >
          <template v-if="!activeFile">
            <template v-if="conversation">
              <div
                ref="scrollArea"
                class="conversation-history"
                aria-label="文档对话历史"
              >
                <el-button
                  v-if="cursor"
                  class="text-button older-messages"
                  link
                  :disabled="olderLoading"
                  @click="older"
                  >{{ olderLoading ? "正在加载…" : "加载更早记录" }}</el-button
                >
                <p v-if="olderError" role="alert" class="feedback error">
                  {{ olderError }}
                </p>
                <p v-if="turnError" role="alert" class="feedback error">
                  {{ turnError }}
                </p>
                <article
                  v-for="record in records"
                  :key="record.id"
                  class="conversation-turn"
                  :data-task-id="record.id"
                >
                  <div class="prompt-block">
                    <div class="user-message">
                      <p>{{ record.prompt }}</p>
                      <div v-if="record.images?.length" class="user-images">
                        <el-image
                          v-for="image in record.images"
                          :key="image.id"
                          class="user-image"
                          :src="imageUrl(image.id)"
                          :preview-src-list="[imageUrl(image.id)]"
                          preview-teleported
                          fit="cover"
                          :alt="image.name"
                        />
                      </div>
                    </div>
                    <TurnActions
                      class="prompt-actions"
                      variant="prompt"
                      :actions="['copy', 'delete']"
                      :copy-text="record.prompt"
                      @select="turnAction(record, $event)"
                    />
                  </div>
                  <TaskProgress
                    :task="record"
                    @view="viewFile"
                    @action="turnAction(record, $event)"
                    @overwrite="confirmOverwrite(record)"
                  />
                </article>
              </div>
              <ConversationRail :turns="anchorTurns" :scroll-area="scrollArea" />
            </template>
            <div v-else class="home-hero">
              <BrandMark class="hero-mark" />
              <h1
                v-if="
                  !historyLoading && !historyError && (!selectedFile || known)
                "
              >
                让文档处理，<br /><span>像对话一样简单</span>
              </h1>
            </div>
            <div
              class="composer-dock"
              :class="{ 'home-dock': !conversation }"
            >
              <p v-if="historyLoading" role="status" class="history-status">
                正在加载文件历史…
              </p>
              <div v-if="historyError" class="history-status">
                <p class="feedback error" role="alert">{{ historyError }}</p>
                <el-button class="text-button" link @click="retry"
                  >重新加载历史</el-button
                >
              </div>
              <div v-if="progressError" class="history-status">
                <p class="feedback error" role="alert">{{ progressError }}</p>
                <el-button
                  v-if="progressError.includes('任务不存在')"
                  class="text-button"
                  link
                  @click="retry"
                  >重新加载历史</el-button
                >
              </div>
              <Composer
                ref="composer"
                v-model="draft"
                :web-enabled="webEnabled"
                @update:web-enabled="setWebEnabled"
                :files="attachments"
                :images="images"
                :conversation="conversation"
                :busy="uploading || taskBusy || historyLoading"
                :project-id="projectId"
                :source-files="mentionFiles"
                :sources-loading="mentionLoading"
                :sources-error="mentionError"
                :project-files-available="!!projectId && sourceCount > 0"
                :can-stop="canStop"
                :stopping="stopPending"
                :send-disabled="!!historyError"
                :message="message"
                :error="error || sendError"
                @attach="attach"
                @clear="detach"
                @send="send"
                @upload="upload"
                @upload-images="uploadImages"
                @remove-image="removeImage"
                @project-files="openProjectFiles"
                @refresh-sources="refreshMentionFiles"
                @stop="stop"
                @invalid="
                  error = $event;
                  message = '';
                "
              />
            </div>
          </template>
          <DocumentEditor
            v-else
            :key="activeFile.id"
            :file="activeFile"
            @close="returnToComposer"
          />
        </main>
        <footer>AI Office Agent</footer>
      </section>
    </div>
    <ProjectFilesDrawer
      v-if="sourceProjectId"
      :project-id="sourceProjectId"
      :selectable="sourceProjectId === projectId"
      :selected-ids="attachments.map((f) => f.id)"
      :busy="taskBusy || uploading"
      @close="sourceProjectId = null"
      @select="drawerAttach"
      @deselect="drawerDetach"
      @deleted="sourceRemoved"
    />
  </el-config-provider>
</template>
<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from "vue";
import {
  ElButton,
  ElConfigProvider,
  ElImage,
  ElMessage,
  ElMessageBox,
  ElTooltip,
} from "element-plus";
import zhCn from "element-plus/es/locale/lang/zh-cn";
import TaskProgress from "./components/TaskProgress.vue";
import DocumentEditor from "./components/DocumentEditor.vue";
import Composer from "./components/Composer.vue";
import ProjectSidebar from "./components/ProjectSidebar.vue";
import BrandMark from "./components/BrandMark.vue";
import ProjectFilesDrawer from "./components/ProjectFilesDrawer.vue";
import TurnActions from "./components/TurnActions.vue";
import ConversationRail from "./components/ConversationRail.vue";
import {
  type TaskState,
  listProjects,
  overwriteLocalTask,
  projectFiles,
  uploadFile,
  uploadImage,
  imageUrl,
  type FileInfo,
  type ProjectInfo,
  type SourceFile,
} from "./api";
import { useDocumentHistory } from "./useDocumentHistory";

const sidebarKey = "office-agent-sidebar-open";
const narrowQuery = "(max-width: 900px)";
function prefersCollapsed() {
  if (typeof window === "undefined") return false;
  return window.matchMedia?.(narrowQuery)?.matches ?? false;
}
function initialSidebar() {
  if (prefersCollapsed()) return false;
  try {
    const saved = localStorage.getItem(sidebarKey);
    if (saved !== null) return saved !== "false";
  } catch {
    /* optional */
  }
  return true;
}
const turnError = ref("");
const projects = ref<ProjectInfo[]>([]),
  total = ref(0),
  loading = ref(false),
  uploading = ref(false),
  sidebarOpen = ref(initialSidebar());
const error = ref(""),
  message = ref(""),
  listError = ref("");
const activeFile = ref<FileInfo | null>(null);
const previewProjectId = ref<string | null>(null);
const selectingProjectId = ref<string | null>(null);
let previewScrollTop: number | null = null;
const sourceProjectId = ref<string | null>(null);
const mentionFiles = ref<SourceFile[]>([]);
const mentionLoading = ref(false);
const mentionError = ref("");
let mentionRequest = 0;
const composer = ref<InstanceType<typeof Composer>>();
const {
  webEnabled,
  setWebEnabled,
  projectId,
  selected: selectedFile,
  sourceCount,
  attachments,
  images,
  addImage,
  removeImage,
  ensureProject,
  attach,
  detach,
  records,
  draft,
  known,
  historyLoading,
  historyError,
  canStop,
  stopPending,
  progressError,
  sendError,
  olderLoading,
  olderError,
  cursor,
  busy: taskBusy,
  conversation,
  scrollArea,
  select,
  retry,
  older,
  clear,
  send,
  regenerate,
  deleteTurn,
  stop,
  restore,
  projectDeleted,
} = useDocumentHistory(() => {
  void load();
  void refreshMentionFiles();
});

const currentProjectName = computed(
  () =>
    projects.value.find((project) => project.id === projectId.value)?.name ||
    "当前会话",
);

const anchorTurns = computed(() =>
  records.value.map((record) => ({ id: record.id, prompt: record.prompt })),
);

async function confirmOverwrite(record: TaskState) {
  const local = record.result?.local_edit;
  if (!local || local.overwritten) return;
  turnError.value = "";
  try {
    await ElMessageBox.confirm(
      `将用本次生成的版本覆盖原文件：${local.path}。覆盖前会自动保留 .bak 备份。`,
      "覆盖源文件",
      {
        confirmButtonText: "覆盖",
        cancelButtonText: "取消",
        type: "warning",
        confirmButtonClass: "delete-confirm-button",
        closeOnClickModal: false,
        lockScroll: false,
      },
    );
  } catch {
    return;
  }
  try {
    const done = await overwriteLocalTask(record.id);
    local.overwritten = { at: new Date().toISOString(), backup: done.backup };
    ElMessage.success("已覆盖源文件，并保留备份。");
  } catch (cause) {
    turnError.value = (cause as Error).message;
  }
}

async function turnAction(record: TaskState, key: "regenerate" | "delete") {
  turnError.value = "";
  if (key === "regenerate") {
    await regenerate(record);
    return;
  }
  try {
    await ElMessageBox.confirm(
      "确定删除这一轮对话吗？提示词和回复会一起删除，无法恢复；这一轮生成的文件会保留在项目中。",
      "删除这一轮对话",
      {
        confirmButtonText: "删除",
        cancelButtonText: "取消",
        type: "warning",
        confirmButtonClass: "delete-confirm-button",
        closeOnClickModal: false,
        lockScroll: false,
      },
    );
  } catch {
    return;
  }
  try {
    await deleteTurn(record);
  } catch (cause) {
    turnError.value = (cause as Error).message;
  }
}

function persistSidebar(open: boolean) {
  try {
    localStorage.setItem(sidebarKey, String(open));
  } catch {
    /* optional */
  }
}
function setSidebar(open: boolean) {
  if (sidebarOpen.value === open) return;
  sidebarOpen.value = open;
  persistSidebar(open);
}
async function toggleSidebar() {
  setSidebar(!sidebarOpen.value);
  await nextTick();
  document
    .querySelector<HTMLElement>(
      sidebarOpen.value
        ? ".sidebar-shell [data-sidebar-toggle]"
        : ".workspace-bar [data-sidebar-toggle]",
    )
    ?.focus();
}
function closeSidebarOnNarrow() {
  if (prefersCollapsed()) setSidebar(false);
}

async function selectProject(project: ProjectInfo) {
  if (taskBusy.value || uploading.value || historyLoading.value) return;
  if (project.id === projectId.value && !historyError.value) return;
  selectingProjectId.value = project.id;
  activeFile.value = null;
  error.value = "";
  message.value = "";
  turnError.value = "";
  try {
    await select(project.id);
  } finally {
    selectingProjectId.value = null;
  }
  closeSidebarOnNarrow();
  await nextTick();
  composer.value?.focus();
}
async function newSession() {
  if (taskBusy.value || uploading.value || historyLoading.value) return;
  clear();
  activeFile.value = null;
  error.value = "";
  message.value = "";
  turnError.value = "";
  closeSidebarOnNarrow();
  await nextTick();
  composer.value?.focus();
}
function viewFile(file: FileInfo) {
  if (!activeFile.value) {
    previewScrollTop = scrollArea.value?.scrollTop ?? null;
  }
  activeFile.value = file;
  previewProjectId.value = projectId.value;
}
function viewProject(project: ProjectInfo) {
  closeSidebarOnNarrow();
  sourceProjectId.value = project.id;
}
function openProjectFiles() {
  if (projectId.value) sourceProjectId.value = projectId.value;
}
async function refreshMentionFiles() {
  const id = projectId.value;
  if (!id) {
    mentionRequest++;
    mentionFiles.value = [];
    mentionLoading.value = false;
    mentionError.value = "";
    return;
  }
  const revision = ++mentionRequest;
  mentionLoading.value = true;
  mentionError.value = "";
  try {
    const result = await projectFiles(id);
    if (revision !== mentionRequest || projectId.value !== id) return;
    mentionFiles.value = result.items;
  } catch (cause) {
    if (revision === mentionRequest)
      mentionError.value = (cause as Error).message;
  } finally {
    if (revision === mentionRequest) mentionLoading.value = false;
  }
}
function drawerAttach(file: FileInfo) {
  attach(file);
  composer.value?.addMention(file);
}
function drawerDetach(id: string) {
  composer.value?.removeMentionById(id);
  detach(id);
}
watch(projectId, (id) => {
  previewScrollTop = null;
  turnError.value = "";
  mentionRequest++;
  mentionFiles.value = [];
  mentionError.value = "";
  mentionLoading.value = false;
  if (id) void refreshMentionFiles();
});
async function returnToComposer() {
  const returnProjectId = previewProjectId.value;
  const returnScrollTop = previewScrollTop;
  previewScrollTop = null;
  activeFile.value = null;
  await nextTick();
  if (activeFile.value || projectId.value !== returnProjectId) return;
  composer.value?.focus();
  // The history is remounted after preview; restore its position after focus.
  if (scrollArea.value && returnScrollTop !== null) {
    scrollArea.value.scrollTop = returnScrollTop;
  }
}
// 一轮回答结束（完成、失败或停止）后把光标交还输入框，方便直接接着问下一句。
// 只有用户没有在别处操作时才抢焦点：正在其它输入框、弹窗或抽屉里，正在选中
// 页面文字，打开着文档预览，或在触屏设备上（避免自动弹出软键盘），都保持原样。
function canTakeFocusBack() {
  if (activeFile.value || sourceProjectId.value || document.hidden) return false;
  if (window.matchMedia?.("(pointer: coarse)")?.matches) return false;
  if (window.getSelection?.()?.toString().trim()) return false;
  const element = document.activeElement as HTMLElement | null;
  if (!element || element === document.body) return true;
  if (
    element.closest('[role="dialog"], .el-message-box, .el-overlay, .el-drawer')
  )
    return false;
  return !element.matches(
    'input, textarea, select, [contenteditable="true"], [contenteditable=""]',
  );
}
watch(taskBusy, async (busy, wasBusy) => {
  if (busy || !wasBusy) return;
  await nextTick();
  if (canTakeFocusBack()) composer.value?.focus({ atEnd: true });
});
function sourceRemoved(file: SourceFile) {
  const activeProject = sourceProjectId.value === projectId.value;
  if (activeProject) {
    composer.value?.removeMentionByName(file.current_file?.name || file.name);
    if (file.current_file) detach(file.current_file.id);
    detach(file.id);
    sourceCount.value = Math.max(0, sourceCount.value - 1);
    mentionFiles.value = mentionFiles.value.filter(
      (item) => item.id !== file.id,
    );
    for (const record of records.value) {
      const result = record.result;
      if (result?.file?.id === file.id) result.file.available = false;
      for (const output of result?.outputs || [])
        if (output.file.id === file.id) output.file.available = false;
    }
  }
  void load();
  if (activeProject) void refreshMentionFiles();
}
function projectRemoved(project: ProjectInfo) {
  const cleared = projectDeleted(project.id);
  if (previewProjectId.value === project.id || cleared) activeFile.value = null;
  if (cleared) {
    error.value = "";
    message.value = "";
  }
  projects.value = projects.value.filter((item) => item.id !== project.id);
  total.value = Math.max(0, total.value - 1);
  void load();
}
let generation = 0;
let listPending = false;
async function load(more = false, showLoading = false) {
  if (more && listPending) return;
  const current = ++generation;
  listPending = true;
  loading.value = showLoading;
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
    if (current === generation) {
      loading.value = false;
      listPending = false;
    }
  }
}

async function uploadImages(files: File[]) {
  if (uploading.value || taskBusy.value || historyLoading.value) return;
  uploading.value = true;
  error.value = "";
  try {
    const id = await ensureProject();
    for (const file of files) {
      try {
        addImage(await uploadImage(file, id));
      } catch (cause) {
        error.value += `图片 ${file.name}：${(cause as Error).message} `;
      }
    }
  } catch (cause) {
    error.value = (cause as Error).message;
  } finally {
    uploading.value = false;
  }
}

async function upload(files: File[]) {
  if (uploading.value || taskBusy.value || historyLoading.value) return;
  uploading.value = true;
  error.value = "";
  try {
    const id = await ensureProject();
    for (const file of files) {
      message.value = `正在上传 ${file.name}…`;
      try {
        attach(await uploadFile(file, id));
        sourceCount.value++;
      } catch (cause) {
        error.value += `${file.name}：${(cause as Error).message} `;
      }
    }
    void refreshMentionFiles();
    void load();
    message.value = "";
  } catch (cause) {
    error.value = (cause as Error).message;
    message.value = "";
  } finally {
    uploading.value = false;
  }
}

onMounted(() => {
  void load();
  void restore();
});
</script>
