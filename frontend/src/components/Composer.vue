<script setup lang="ts">
import { computed, ref } from "vue";
import type { FileInfo } from "../api";
const props = defineProps<{ busy: boolean; webEnabled?: boolean; sendDisabled?: boolean; conversation?: boolean; message: string; error: string; file?: FileInfo | null; files?: FileInfo[] }>();
const emit = defineEmits<{
  'update:webEnabled': [value: boolean];
  clear: [id: string];
  send: [];
  upload: [files: File[]];
  invalid: [message: string];
}>();
const draft = defineModel<string>({ default: "" });
const textarea = ref<HTMLTextAreaElement>();
const composing = ref(false);
const canSend = computed(() => !props.busy && !props.sendDisabled && !!draft.value.trim());
function send() {
  if (canSend.value) emit("send");
}
function keydown(event: KeyboardEvent) {
  // IME Enter confirms a candidate; keyCode 229 covers browsers that end
  // composition before dispatching the confirmation keydown.
  if (event.key !== "Enter" || event.shiftKey || composing.value || event.isComposing || event.keyCode === 229) return;
  event.preventDefault();
  if (!event.repeat) send();
}
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
  const selected = Array.from(files);
  if (selected.length > 20) return emit('invalid', '每次最多选择 20 份文件。');
  for (const file of selected) {
    if (!file.name.toLowerCase().endsWith('.docx')) return emit('invalid', '目前仅支持 .docx 文件。');
    if (file.size === 0) return emit('invalid', '不能上传空文件。');
    if (file.size > 10 * 1024 * 1024) return emit('invalid', '文件不能超过 10 MiB。');
  }
  emit('upload', selected);
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
    <div v-for="file in (files || (file ? [file] : []))" :key="file.id" class="selected-file">
      <span class="selected-file-icon" aria-hidden="true">W</span>
      <span class="selected-file-name" :title="file.name">{{ file.name }}</span>
      <button type="button" class="icon-button" aria-label="清除当前选中文件" title="取消选择，不删除已上传文件" :disabled="busy" @click="emit('clear', file.id)">×</button>
    </div>
    <textarea
      ref="textarea"
      v-model="draft"
      aria-label="输入文档问题或修改要求"
      placeholder="聊点什么，或上传文件进行总结、提问和修改…"
      rows="3"
      :disabled="busy"
      maxlength="2000"
      @input="resize"
      @keydown="keydown"
      @compositionstart="composing = true"
      @compositionend="composing = false"
      title="Enter 发送，Shift + Enter 换行"
    ></textarea>
    <div class="composer-toolbar">
      <input
        ref="picker"
        class="visually-hidden"
        type="file"
        multiple
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
        busy ? "正在处理…" : "支持多份 .docx · 每份最大 10 MiB"
      }}</span>
      <button type="button" class="web-toggle" role="switch" aria-label="联网" :aria-checked="webEnabled !== false"
        :disabled="busy" :title="webEnabled !== false ? '需要实时信息时自动搜索，也可读取网页链接' : '联网已关闭，本轮不会搜索或读取网页'"
        @click="emit('update:webEnabled', webEnabled === false)">
        <span aria-hidden="true">◎</span> 联网
      </button>
      <span class="ai-hint"></span>
      <button
        class="send-button"
        type="button"
        :disabled="!canSend"
        aria-label="发送处理要求"
        @click="send"
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
  <p v-else class="composer-note">{{ conversation ? "可直接聊天；仅处理本轮选中的附件" : "直接发送开始聊天，也可拖入多份 Word 文档" }}</p>
</template>
