<template>
  <el-drawer
    v-model="open"
    class="files-drawer"
    direction="rtl"
    size="460px"
    :with-header="false"
    :before-close="beforeClose"
    append-to-body
    modal-class="files-drawer-overlay"
    @closed="emit('close')"
  >
    <div class="files-drawer-panel">
      <header class="files-drawer-head">
        <div class="files-head-copy">
          <div class="files-title-row">
            <h2 id="project-files-title">项目文件</h2>
            <span class="files-count">{{ items.length }}</span>
          </div>
          <p>
            {{
              selectable
                ? "选择本轮要处理的附件，或管理项目文件"
                : "管理项目文件"
            }}
          </p>
        </div>
        <div class="files-head-actions">
          <el-tooltip content="刷新列表" placement="bottom" :show-after="400">
            <el-button
              class="icon-button"
              text
              circle
              aria-label="刷新项目文件"
              :disabled="loading"
              @click="load"
            >
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="1.7"
                aria-hidden="true"
              >
                <path d="M20 11a8 8 0 1 0-2.2 5.6" stroke-linecap="round" />
                <path d="M20 4.5V11h-6.4" stroke-linecap="round" />
              </svg>
            </el-button>
          </el-tooltip>
          <el-tooltip content="关闭" placement="bottom" :show-after="400">
            <el-button
              class="icon-button"
              text
              circle
              aria-label="关闭项目文件"
              @click="requestClose"
            >
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="1.7"
                aria-hidden="true"
              >
                <path d="m6.5 6.5 11 11m0-11-11 11" stroke-linecap="round" />
              </svg>
            </el-button>
          </el-tooltip>
        </div>
      </header>

      <el-scrollbar class="files-scroll">
        <div class="files-drawer-body" :aria-busy="loading">
          <p v-if="loading" class="visually-hidden" role="status">
            正在加载项目文件…
          </p>
          <el-skeleton v-if="loading" :rows="6" animated />

          <el-empty
            v-else-if="!items.length && !error"
            :image-size="42"
            description="还没有项目文件"
          >
            <p class="files-empty-hint">
              在输入框中上传 DOCX，<br />或直接发送消息生成文档。
            </p>
          </el-empty>

          <ul v-if="items.length" class="file-list-rows">
            <li
              v-for="row in items"
              :key="row.id"
              class="file-row"
              :class="{
                'is-selected': isSelected(row),
                'is-unavailable': !row.available,
              }"
              data-testid="source-row"
            >
              <el-checkbox
                v-if="selectable"
                class="file-check"
                :model-value="isSelected(row)"
                :disabled="
                  busy ||
                  deleting ||
                  !row.current_file ||
                  row.current_file.available === false
                "
                :aria-label="`选择 ${row.name}`"
                data-testid="source-select"
                @change="toggle(row, $event === true)"
              />
              <span
                class="file-kind"
                :class="{ generated: row.origin === 'generated' }"
                aria-hidden="true"
              >
                <svg
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="1.6"
                >
                  <path
                    d="M6.5 3.8h7.1l4.4 4.4v11.9a1.6 1.6 0 0 1-1.6 1.6H6.5a1.6 1.6 0 0 1-1.6-1.6V5.4a1.6 1.6 0 0 1 1.6-1.6Z"
                    stroke-linejoin="round"
                  />
                  <path d="M13.3 3.9v4.6h4.6" stroke-linejoin="round" />
                  <path d="M8.7 12h5.2M8.7 15h5.2" stroke-linecap="round" />
                </svg>
              </span>
              <div class="file-copy">
                <div class="file-name-line">
                  <span class="file-name" :title="row.name">{{ row.name }}</span>
                </div>
                <p class="file-meta">
                  <el-tag
                    class="file-origin"
                    :class="row.origin === 'generated' ? 'generated' : 'uploaded'"
                    size="small"
                    effect="plain"
                    >{{ row.origin === "generated" ? "生成" : "上传" }}</el-tag
                  >
                  <span>{{ row.size == null ? "未知" : formatSize(row.size) }}</span>
                  <span class="meta-dot" aria-hidden="true">·</span>
                  <span>{{ formatDate(row.created_at) }}</span>
                  <span v-if="!row.available" class="file-missing"
                    >文件不可用</span
                  >
                </p>
              </div>
              <div class="file-actions">
                <el-tooltip
                  v-if="row.available"
                  content="下载"
                  placement="top"
                  :show-after="400"
                >
                  <el-button
                    tag="a"
                    class="file-icon-button download"
                    text
                    circle
                    :href="downloadUrl(row.id)"
                    :aria-label="`下载 ${row.name}`"
                    data-testid="source-download"
                  >
                    <svg
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      stroke-width="1.7"
                      aria-hidden="true"
                    >
                      <path d="M12 4v11" stroke-linecap="round" />
                      <path d="m7.4 10.6 4.6 4.6 4.6-4.6" stroke-linecap="round" />
                      <path d="M5 19.2h14" stroke-linecap="round" />
                    </svg>
                  </el-button>
                </el-tooltip>
                <el-tooltip content="预览" placement="top" :show-after="400">
                  <el-button
                    class="file-icon-button preview"
                    text
                    circle
                    :disabled="!row.available || deleting"
                    :aria-label="`预览 ${row.name}`"
                    data-testid="source-preview"
                    @click="openPreview(row)"
                  >
                    <svg
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      stroke-width="1.7"
                      aria-hidden="true"
                    >
                      <path
                        d="M2.8 12s3.4-5.5 9.2-5.5S21.2 12 21.2 12s-3.4 5.5-9.2 5.5S2.8 12 2.8 12Z"
                        stroke-linejoin="round"
                      />
                      <circle cx="12" cy="12" r="2.4" />
                    </svg>
                  </el-button>
                </el-tooltip>
                <el-tooltip content="删除" placement="top" :show-after="400">
                  <el-button
                    class="file-icon-button delete"
                    text
                    circle
                    :disabled="busy || deleting"
                    :aria-label="`删除 ${row.name}`"
                    data-testid="source-delete"
                    @click="confirmDelete(row)"
                  >
                    <svg
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      stroke-width="1.7"
                      aria-hidden="true"
                    >
                      <path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13" />
                      <path d="M10 10.5v6m4-6v6" stroke-linecap="round" />
                    </svg>
                  </el-button>
                </el-tooltip>
              </div>
            </li>
          </ul>

          <el-alert
            v-if="error"
            class="files-alert"
            type="error"
            :closable="false"
            :title="error"
          />
          <div v-if="error" class="files-retry">
            <el-button
              class="text-button"
              link
              :disabled="loading"
              @click="load"
              >重新加载</el-button
            >
          </div>
          <el-alert
            v-if="deleteError"
            class="files-alert"
            type="error"
            :closable="false"
            :title="deleteError"
          />
          <p v-if="deleting" class="files-status" role="status">正在删除…</p>
        </div>
      </el-scrollbar>

      <footer class="files-drawer-foot">
        <span v-if="selectable" class="files-selected-count"
          >已选 {{ selectedIds?.length || 0 }}</span>
        <p v-if="selectable">
          勾选的文件用于本轮对话；已有修改结果时处理其最新版本
        </p>
        <p v-else>项目文件与对话记录保存在本地。</p>

      </footer>
    </div>
  </el-drawer>

  <el-dialog
    v-model="previewOpen"
    class="preview-dialog"
    title="文件预览"
    width="min(1080px, 94vw)"
    top="4vh"
    append-to-body
    destroy-on-close
    @closed="preview = null"
  >
    <DocumentEditor
      v-if="preview"
      :key="preview.id"
      :file="preview"
      :show-back="false"
      @close="previewOpen = false"
    />
  </el-dialog>
</template>
<script setup lang="ts">
import { onMounted, ref } from "vue";
import {
  ElAlert,
  ElButton,
  ElCheckbox,
  ElDialog,
  ElDrawer,
  ElEmpty,
  ElMessageBox,
  ElScrollbar,
  ElSkeleton,
  ElTag,
  ElTooltip,
} from "element-plus";
import DocumentEditor from "./DocumentEditor.vue";
import {
  deleteSourceFile,
  downloadUrl,
  formatSize,
  projectFiles,
  type FileInfo,
  type SourceFile,
} from "../api";

const props = defineProps<{
  projectId: string;
  selectable?: boolean;
  selectedIds?: string[];
  busy?: boolean;
}>();
const emit = defineEmits<{
  close: [];
  select: [file: FileInfo];
  deselect: [id: string];
  deleted: [file: SourceFile];
}>();

const open = ref(true);
const items = ref<SourceFile[]>([]);
const loading = ref(false);
const error = ref("");
const deleting = ref(false);
const deleteError = ref("");
const preview = ref<FileInfo | null>(null);
const previewOpen = ref(false);

function requestClose() {
  if (deleting.value) return;
  open.value = false;
}
function beforeClose(done: () => void) {
  if (deleting.value) return;
  done();
}
function isSelected(row: SourceFile) {
  return (
    !!row.current_file && !!props.selectedIds?.includes(row.current_file.id)
  );
}
function formatDate(value: string | null) {
  if (!value) return "未知";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "未知";
  return date.toLocaleString("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
function toggle(row: SourceFile, checked: boolean) {
  if (!row.current_file) return;
  if (checked) emit("select", row.current_file);
  else emit("deselect", row.current_file.id);
}
function openPreview(row: SourceFile) {
  if (!row.available) return;
  preview.value = row;
  previewOpen.value = true;
}
async function confirmDelete(row: SourceFile) {
  if (props.busy || deleting.value) return;
  deleteError.value = "";
  try {
    await ElMessageBox.confirm(
      `确定删除“${row.name}”吗？项目文件将永久删除，聊天记录和已有修改结果保留。`,
      "删除项目文件",
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
  deleting.value = true;
  try {
    await deleteSourceFile(props.projectId, row.id);
    items.value = items.value.filter((item) => item.id !== row.id);
    emit("deleted", row);
    if (preview.value?.id === row.id) previewOpen.value = false;
  } catch (cause) {
    deleteError.value = (cause as Error).message;
  } finally {
    deleting.value = false;
  }
}
async function load() {
  loading.value = true;
  error.value = "";
  try {
    items.value = (await projectFiles(props.projectId)).items;
  } catch (cause) {
    error.value = (cause as Error).message;
  } finally {
    loading.value = false;
  }
}
onMounted(() => {
  void load();
});
</script>
