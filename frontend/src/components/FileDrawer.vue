<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import { downloadUrl, formatSize, type FileInfo } from "../api";
defineProps<{
  files: FileInfo[];
  total: number;
  loading: boolean;
  error: string;
}>();
const emit = defineEmits<{ close: []; more: []; retry: [] }>();
const panel = ref<HTMLElement>();
const closeButton = ref<HTMLButtonElement>();
const previousOverflow = document.body.style.overflow;
function keyboard(event: KeyboardEvent) {
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
    <div class="drawer-backdrop" @click.self="emit('close')">
      <aside
        ref="panel"
        class="drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="drawer-title"
      >
        <div class="drawer-heading">
          <div>
            <h2 id="drawer-title">已上传文件</h2>
            <p>共 {{ total }} 个文件</p>
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
          <div v-if="!files.length && !loading && !error" class="empty">
            <span class="empty-icon">▤</span><strong>还没有上传文件</strong>
            <p>通过输入框的附件按钮，<br />或拖入一份 DOCX 文档。</p>
          </div>
          <article v-for="file in files" :key="file.id" class="file-card">
            <div class="file-symbol" aria-hidden="true">W</div>
            <div class="file-details">
              <h3 :title="file.name">{{ file.name }}</h3>
              <p>
                {{ formatSize(file.size) }} ·
                {{ new Date(file.created_at).toLocaleString("zh-CN") }}
              </p>
              <a :href="downloadUrl(file.id)" :aria-label="`下载 ${file.name}`"
                >下载原文件 <span aria-hidden="true">↓</span></a
              >
            </div>
          </article>
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
            v-if="files.length < total && !error"
            class="load-more"
            :disabled="loading"
            @click="emit('more')"
          >
            加载更多
          </button>
        </div>
        <div class="drawer-footer">原始文件已保存，可随时下载</div>
      </aside>
    </div>
  </Teleport>
</template>
