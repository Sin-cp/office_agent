<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import { ElMessageBox } from "element-plus";
import "element-plus/theme-chalk/base.css";
import "element-plus/theme-chalk/el-message-box.css";
import "element-plus/theme-chalk/el-overlay.css";
import "element-plus/theme-chalk/el-button.css";
import "element-plus/theme-chalk/el-input.css";
import { deleteProject, renameProject, type ProjectInfo } from "../api";
const props = defineProps<{
  busy?: boolean;
  projects: ProjectInfo[];
  total: number;
  loading: boolean;
  error: string;
}>();
const emit = defineEmits<{ renamed: []; deleted: [file: ProjectInfo]; close: []; more: []; retry: []; edit: [file: ProjectInfo]; view: [file: ProjectInfo] }>();
const confirming = ref(false);
const deleting = ref(false);
const deleteError = ref("");
async function remove(file: ProjectInfo) {
  if (props.busy || confirming.value || deleting.value) return;
  confirming.value = true;
  deleteError.value = "";
  try {
    await ElMessageBox.confirm(`确定删除项目“${file.name}”吗？项目文件、全部修改版和对话历史将一并删除，无法恢复！`, "删除项目", {
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
async function rename(project: ProjectInfo) {
  confirming.value = true;
  deleteError.value = '';
  try {
    const result = await ElMessageBox.prompt('输入项目名称', '重命名项目', {
      customClass: 'project-rename-dialog',
      inputPlaceholder: '为项目起一个容易识别的名称',
      inputValue: project.name, inputValidator: value => !!value?.trim() && value.trim().length <= 100 || '请输入 1–100 个字符',
      confirmButtonText: '保存', cancelButtonText: '取消', closeOnClickModal: false, lockScroll: false,
    });
    await renameProject(project.id, result.value.trim());
    emit('renamed');
  } catch (cause) {
    if (cause instanceof Error) deleteError.value = cause.message;
  } finally { confirming.value = false; await nextTick(); closeButton.value?.focus(); }
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
            <h2 id="drawer-title">项目管理</h2>
            <p>共 {{ total }} 个项目</p>
          </div>
          <button
            ref="closeButton"
            class="icon-button"
            aria-label="关闭项目管理"
            @click="emit('close')"
          >
            ×
          </button>
        </div>
        <div class="file-list" :aria-busy="loading">
          <div v-if="!projects.length && !loading && !error" class="empty">
            <span class="empty-icon">▤</span><strong>还没有项目</strong>
            <p>直接发送消息开始聊天，<br />也可以上传 DOCX 文档。</p>
          </div>
          <article v-for="file in projects" :key="file.id" class="file-card">
            <div class="file-symbol" aria-hidden="true">{{ file.file_count ? 'W' : '聊' }}</div>
            <div class="file-details">
              <h3 :title="file.name">{{ file.name }} <button class="text-button rename-project" :aria-label="`重命名项目 ${file.name}`" :disabled="confirming || deleting" @click="rename(file)">✎</button></h3>
              <p>
                {{ file.file_count }} 个文件 ·
                {{ file.created_at ? new Date(file.created_at).toLocaleString('zh-CN') : '上传时间未知' }}
              </p>
              <button class="text-button file-edit" v-if="file.file_count" @click="emit('view', file)">项目文件</button>
              <button class="text-button file-edit" :disabled="busy" @click="emit('edit', file)">继续聊天</button>

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
          <p v-if="loading" class="list-status" role="status">正在加载项目…</p>
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

<!-- MessageBox is mounted on body, so its styles must be global and class-scoped. -->
<style>
.project-rename-dialog.el-message-box {
  --el-color-primary: #6264d9;
  --el-color-primary-light-3: #8183e5;
  --el-color-primary-dark-2: #5052bf;
  width: 440px;
  max-width: calc(100vw - 32px);
  padding: 24px;
  border: 1px solid #e6e8f0;
  border-radius: 16px;
  box-shadow: 0 20px 64px #18233e26;
}
.project-rename-dialog .el-message-box__header { padding: 0 32px 18px 0; }
.project-rename-dialog .el-message-box__title { color: #202938; font-size: 20px; font-weight: 600; }
.project-rename-dialog .el-message-box__headerbtn { top: 20px; right: 20px; }
.project-rename-dialog .el-message-box__content { padding: 0; }
.project-rename-dialog .el-message-box__message { color: #69758a; font-size: 14px; }
.project-rename-dialog .el-message-box__input { padding-top: 10px; }
.project-rename-dialog .el-input { width: 100%; }
.project-rename-dialog .el-input__wrapper {
  padding: 4px 14px;
  border-radius: 10px;
  background: #f8f9fc;
  box-shadow: 0 0 0 1px #dce1ed inset;
  transition: box-shadow .15s, background-color .15s;
}
.project-rename-dialog .el-input__wrapper:hover { box-shadow: 0 0 0 1px #b2b8d3 inset; }
.project-rename-dialog .el-input__wrapper.is-focus {
  background: #fff;
  box-shadow: 0 0 0 1px #6264d9 inset, 0 0 0 3px #6264d91a;
}
.project-rename-dialog .el-input__inner { height: 36px; font-size: 15px; color: #202938; }
.project-rename-dialog .el-message-box__input.is-error .el-input__wrapper { box-shadow: 0 0 0 1px #dc4545 inset; }
.project-rename-dialog .el-message-box__errormsg { padding-top: 6px; font-size: 12px; }
.project-rename-dialog .el-message-box__btns { padding: 20px 0 0; gap: 10px; }
.project-rename-dialog .el-message-box__btns .el-button { min-width: 80px; height: 36px; margin: 0; border-radius: 8px; font-size: 14px; }
</style>
