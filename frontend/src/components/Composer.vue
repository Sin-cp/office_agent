<script setup lang="ts">
import { ref } from "vue";
import type { FileInfo } from "../api";
const props = defineProps<{ busy: boolean; message: string; error: string; file?: FileInfo | null }>();
const emit = defineEmits<{
  clear: [];
  upload: [file: File];
  invalid: [message: string];
}>();
const draft = defineModel<string>({ default: "" });
const textarea = ref<HTMLTextAreaElement>();
defineExpose({ focus: () => textarea.value?.focus() });
const picker = ref<HTMLInputElement>();
const dragDepth = ref(0);
function resize(event: Event) {
  const area = event.target as HTMLTextAreaElement;
  area.style.height = "auto";
  area.style.height = `${Math.min(area.scrollHeight, 240)}px`;
}
function select(files: FileList | null) {
  if (props.busy || !files?.length) return;
  if (files.length !== 1)
    return emit("invalid", "每次只能上传一个文件，请重新选择。");
  const file = files[0]!;
  if (!file.name.toLowerCase().endsWith(".docx"))
    return emit("invalid", "目前仅支持 .docx 文件。");
  if (file.size === 0) return emit("invalid", "不能上传空文件。");
  if (file.size > 10 * 1024 * 1024)
    return emit("invalid", "文件不能超过 10 MiB。");
  emit("upload", file);
}
function picked(event: Event) {
  const input = event.target as HTMLInputElement;
  select(input.files);
  input.value = "";
}
function dropped(event: DragEvent) {
  dragDepth.value = 0;
  select(event.dataTransfer?.files ?? null);
}
</script>
<template>
  <section
    class="composer"
    :class="{ dragging: dragDepth > 0 }"
    aria-label="文档编辑输入框"
    @dragenter.prevent="dragDepth++"
    @dragleave.prevent="dragDepth = Math.max(0, dragDepth - 1)"
    @dragover.prevent
    @drop.prevent="dropped"
  >
    <div v-if="file" class="selected-file">
      <span class="selected-file-icon" aria-hidden="true">W</span>
      <span class="selected-file-name" :title="file.name">{{ file.name }}</span>
      <button type="button" class="icon-button" aria-label="清除当前选中文件" title="取消选择，不删除已上传文件" :disabled="busy" @click="emit('clear')">×</button>
    </div>
    <textarea
      ref="textarea"
      v-model="draft"
      aria-label="输入文档修改要求"
      placeholder="想怎样修改文档？在这里写下你的想法…"
      rows="3"
      @input="resize"
    ></textarea>
    <div class="composer-toolbar">
      <input
        ref="picker"
        class="visually-hidden"
        type="file"
        accept=".docx"
        tabindex="-1"
        aria-label="选择 DOCX 文件"
        :disabled="busy"
        @change="picked"
      />
      <button
        class="icon-button attach"
        type="button"
        aria-label="上传 DOCX 文件"
        title="上传 DOCX 文件"
        :disabled="busy"
        @click="picker?.click()"
      >
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="1.7"
          aria-hidden="true"
        >
          <path d="m8 12 6-6a3 3 0 0 1 4 4l-8 8a5 5 0 0 1-7-7l9-9" />
          <path d="m6 14 8-8" />
        </svg>
      </button>
      <span class="upload-hint">{{
        busy ? "正在上传…" : "仅支持 .docx · 最大 10 MiB"
      }}</span>
      <span class="ai-hint">AI 编辑即将接入</span>
      <button
        class="send-button"
        type="button"
        disabled
        aria-label="发送（AI 编辑即将接入）"
      >
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          aria-hidden="true"
        >
          <path d="M12 19V5m-6 6 6-6 6 6" />
        </svg>
      </button>
    </div>
    <div v-if="dragDepth" class="drop-overlay">
      {{ busy ? "正在上传，请稍候" : "松开即可上传 DOCX 文件" }}
    </div>
  </section>
  <p v-if="error" class="feedback error" role="alert">{{ error }}</p>
  <p v-else-if="message" class="feedback" role="status">{{ message }}</p>
  <p v-else class="composer-note">拖入 Word 文档，开始整理你的文件</p>
</template>
