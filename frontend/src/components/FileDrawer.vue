<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import { ElMessageBox } from "element-plus";
import "element-plus/theme-chalk/base.css";
import "element-plus/theme-chalk/el-message-box.css";
import "element-plus/theme-chalk/el-overlay.css";
import "element-plus/theme-chalk/el-button.css";
import { deleteProject, downloadUrl, formatSize, type ProjectInfo } from "../api";
const props = defineProps<{
  busy?: boolean;
  projects: ProjectInfo[];
  total: number;
  loading: boolean;
  error: string;
}>();
const emit = defineEmits<{ deleted: [file: ProjectInfo]; close: []; more: []; retry: []; edit: [file: ProjectInfo]; view: [file: ProjectInfo] }>();
const confirming = ref(false);
const deleting = ref(false);
const deleteError = ref("");
async function remove(file: ProjectInfo) {
  if (props.busy || confirming.value || deleting.value) return;
  confirming.value = true;
  deleteError.value = "";
  try {
    await ElMessageBox.confirm(`确定删除项目“${file.name}”吗？源文件、全部修改版和对话历史将一并删除，无法恢复！`, "删除项目", {
      confirmButtonText: "确认删除", cancelButtonText: "取消", type: "warning",
      confirmButtonClass: "delete-confirm-button", closeOnClickModal: false,
      lockScroll: false,
    });
  } catch {
    return;
  } finally {
    confirming.value = false;
  }
  deleting.value = true;
  try {
    if (props.busy) return;
    await deleteProject(file.id);
    emit("deleted", file);
  } catch (cause) {
    deleteError.value = (cause as Error).message;
  } finally {
    deleting.value = false;
    await nextTick();
    closeButton.value?.focus();
  }
}
const panel = ref<HTMLElement>();
const closeButton = ref<HTMLButtonElement>();
const previousOverflow = document.body.style.overflow;
function keyboard(event: KeyboardEvent) {
  if (confirming.value) return;
  if (event.key === "Escape") {
    event.preventDefault();
    emit("close");
  }
  if (event.key !== "Tab") return;
  const elements = panel.value?.querySelectorAll<HTMLElement>(
    "button:not(:disabled), a[href]",
  );
  if (!elements?.length) return;
  const first = elements[0]!,
    last = elements[elements.length - 1]!;
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}
onMounted(async () => {
  document.body.style.overflow = "hidden";
  document.addEventListener("keydown", keyboard);
  await nextTick();
  closeButton.value?.focus();
});
onBeforeUnmount(() => {
  document.body.style.overflow = previousOverflow;
  document.removeEventListener("keydown", keyboard);
});
</script>
<template>
  <Teleport to="body">
    <div class="drawer-backdrop" @click.self="!confirming && emit('close')">
      <aside
        ref="panel"
        class="drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="drawer-title"
      >
        <div class="drawer-heading">
          <div>
            <h2 id="drawer-title">文件管理</h2>
            <p>共 {{ total }} 个项目</p>
          </div>
          <button
            ref="closeButton"
            class="icon-button"
            aria-label="关闭文件抽屉"
            @click="emit('close')"
          >
            ×
          </button>
        </div>
        <div class="file-list" :aria-busy="loading">
          <div v-if="!projects.length && !loading && !error" class="empty">
            <span class="empty-icon">▤</span><strong>还没有上传文件</strong>
            <p>通过输入框的附件按钮，<br />或拖入一份 DOCX 文档。</p>
          </div>
          <article v-for="file in projects" :key="file.id" class="file-card">
            <div class="file-symbol" aria-hidden="true">W</div>
            <div class="file-details">
              <h3 :title="file.name">{{ file.name }}</h3>
              <p>
                {{ file.size === null ? '大小未知' : formatSize(file.size) }} ·
                {{ file.created_at ? new Date(file.created_at).toLocaleString('zh-CN') : '上传时间未知' }}
              </p>
              <button class="text-button file-edit" :disabled="!file.source_available" @click="emit('view', file)">预览源文件</button>
              <button class="text-button file-edit" :disabled="busy" @click="emit('edit', file)">修改文件</button>
              <small v-if="!file.source_available">源文件不可用</small>
              <a v-if="file.source_available && file.source_file_id" :href="downloadUrl(file.source_file_id)" :aria-label="`下载源文件 ${file.name}`"
                >下载源文件 <span aria-hidden="true">↓</span></a
              >
            </div>
            <button class="icon-button delete-file" :aria-label="`删除项目 ${file.name}`" title="删除整个项目" :disabled="busy || confirming || deleting" @click="remove(file)">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 10v7m4-7v7" /></svg>
            </button>
          </article>
          <p v-if="deleteError" class="feedback error" role="alert">{{ deleteError }}</p>
          <p v-if="deleting" role="status">正在删除项目…</p>
          <p v-if="error" class="feedback error" role="alert">{{ error }}</p>
          <button
            v-if="error"
            class="text-button"
            :disabled="loading"
            @click="emit('retry')"
          >
            重新加载
          </button>
          <p v-if="loading" class="list-status" role="status">正在加载文件…</p>
          <button
            v-if="projects.length < total && !error"
            class="load-more"
            :disabled="loading"
            @click="emit('more')"
          >
            加载更多
          </button>
        </div>
      </aside>
    </div>
  </Teleport>
</template>
