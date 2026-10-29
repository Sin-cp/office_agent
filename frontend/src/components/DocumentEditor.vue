<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue';
import { renderAsync } from 'docx-preview';
import { downloadUrl, type FileInfo } from '../api';
const props = defineProps<{ file: FileInfo }>();
const emit = defineEmits<{ close: [] }>();
const error = ref('');
const loading = ref(false);
const preview = ref('');
const heading = ref<HTMLElement>();
let controller: AbortController | undefined;
let generation = 0;
async function read() {
  const current = ++generation;
  controller?.abort();
  controller = new AbortController();
  const timeout = window.setTimeout(() => controller?.abort(), 60000);
  loading.value = true;
  error.value = '';
  preview.value = '';
  try {
    const response = await fetch(downloadUrl(props.file.id), { signal: controller.signal });
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      throw new Error(typeof body?.detail === 'string' ? body.detail : '读取文件失败，请重试');
    }
    const data = await response.arrayBuffer();
    const container = document.createElement('div');
    await renderAsync(data, container, undefined, {
      useBase64URL: true,
      renderAltChunks: false,
      ignoreLastRenderedPageBreak: false,
    });
    if (current !== generation) return;
    // Isolate document styles and links from the application; no scripts or remote resources.
    preview.value = `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:;"><style>body{margin:0;background:#eef1f6}.docx-wrapper{padding:24px!important;background:transparent!important}section.docx{margin-left:auto;margin-right:auto}a{pointer-events:none}</style></head><body>${container.innerHTML}</body></html>`;
  } catch (cause) {
    if (current === generation) error.value = cause instanceof Error && cause.name !== 'AbortError'
      ? cause.message : '读取超时，请重新读取文档';
  } finally {
    window.clearTimeout(timeout);
    if (current === generation) loading.value = false;
  }
}
onMounted(() => { heading.value?.focus(); void read(); });
onBeforeUnmount(() => { ++generation; controller?.abort(); });
</script>
<template>
  <section class="document-editor document-viewer" aria-label="查看文件内容">
    <div class="editor-header">
      <h2 ref="heading" tabindex="-1">{{ file.name }}</h2>
      <div class="viewer-actions">
        <button class="text-button" :disabled="loading" @click="read">重新读取文档</button>
        <button class="text-button" @click="emit('close')">返回</button>
      </div>
    </div>
    <div class="document-preview" :aria-busy="loading">
      <p v-if="loading" class="preview-status" role="status">正在加载文档预览…</p>
      <p v-else-if="error" class="preview-status error" role="alert">{{ error }}</p>
      <iframe v-else-if="preview" :srcdoc="preview" sandbox="" title="Word 文档预览" referrerpolicy="no-referrer"></iframe>
    </div>
  </section>
</template>
