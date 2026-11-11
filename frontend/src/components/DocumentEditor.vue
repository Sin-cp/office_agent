<template>
  <section class="document-editor document-viewer" aria-label="查看文件内容">
    <div class="editor-header">
      <h2 ref="heading" tabindex="-1">{{ file.name }}</h2>
      <div class="viewer-actions">
        <el-tooltip content="重新读取文档" placement="bottom" :show-after="400">
          <el-button
            class="viewer-reload"
            :disabled="loading"
            aria-label="重新读取文档"
            @click="read"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">
              <path d="M20 11a8 8 0 1 0-2.2 5.6" stroke-linecap="round" />
              <path d="M20 4.5V11h-6.4" stroke-linecap="round" />
            </svg>
            <span>重新读取</span>
          </el-button>
        </el-tooltip>
        <el-tooltip
          v-if="showBack"
          :content="closeLabel"
          placement="bottom"
          :show-after="400"
        >
          <el-button
            class="viewer-icon-button close"
            text
            circle
            :aria-label="closeLabel"
            @click="emit('close')"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">
              <path d="m6.5 6.5 11 11m0-11-11 11" stroke-linecap="round" />
            </svg>
          </el-button>
        </el-tooltip>
      </div>
    </div>
    <div class="document-preview" :aria-busy="loading">
      <template v-if="loading">
        <p class="visually-hidden" role="status">正在加载文档预览…</p>
        <el-skeleton class="preview-status" :rows="4" animated />
      </template>
      <el-alert
        v-else-if="error"
        class="preview-status error"
        type="error"
        :closable="false"
        :title="error"
      />
      <iframe
        v-else-if="preview"
        :srcdoc="preview"
        sandbox=""
        title="Word 文档预览"
        referrerpolicy="no-referrer"
      ></iframe>
    </div>
  </section>
</template>
<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import { ElAlert, ElButton, ElSkeleton, ElTooltip } from "element-plus";
import { renderAsync } from "docx-preview";
import { downloadUrl, type FileInfo } from "../api";
const props = withDefaults(
  defineProps<{ file: FileInfo; showBack?: boolean; closeLabel?: string }>(),
  { showBack: true, closeLabel: "返回" },
);
const emit = defineEmits<{ close: [] }>();
const error = ref("");
const loading = ref(false);
const preview = ref("");
const heading = ref<HTMLElement>();
let controller: AbortController | undefined;
let generation = 0;
async function read() {
  const current = ++generation;
  controller?.abort();
  controller = new AbortController();
  const timeout = window.setTimeout(() => controller?.abort(), 60000);
  loading.value = true;
  error.value = "";
  preview.value = "";
  try {
    const response = await fetch(downloadUrl(props.file.id), {
      signal: controller.signal,
    });
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      throw new Error(
        typeof body?.detail === "string" ? body.detail : "读取文件失败，请重试",
      );
    }
    const data = await response.arrayBuffer();
    const container = document.createElement("div");
    await renderAsync(data, container, undefined, {
      useBase64URL: true,
      renderAltChunks: false,
      ignoreLastRenderedPageBreak: false,
    });
    if (current !== generation) return;
    // Isolate document styles and links from the application; no scripts or remote resources.
    preview.value = `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:;"><style>body{margin:0;background:#eef1f6}.docx-wrapper{padding:24px!important;background:transparent!important}section.docx{margin-left:auto;margin-right:auto}a{pointer-events:none}</style></head><body>${container.innerHTML}</body></html>`;
  } catch (cause) {
    if (current === generation)
      error.value =
        cause instanceof Error && cause.name !== "AbortError"
          ? cause.message
          : "读取超时，请重新读取文档";
  } finally {
    window.clearTimeout(timeout);
    if (current === generation) loading.value = false;
  }
}
onMounted(() => {
  heading.value?.focus();
  void read();
});
onBeforeUnmount(() => {
  ++generation;
  controller?.abort();
});
</script>
