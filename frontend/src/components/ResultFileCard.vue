<template>
  <article class="result-file" data-testid="result-file">
    <span class="result-file-icon" :class="kind" aria-hidden="true">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6">
        <path
          d="M6.5 3.8h7.1l4.4 4.4v11.9a1.6 1.6 0 0 1-1.6 1.6H6.5a1.6 1.6 0 0 1-1.6-1.6V5.4a1.6 1.6 0 0 1 1.6-1.6Z"
          stroke-linejoin="round"
        />
        <path d="M13.3 3.9v4.6h4.6" stroke-linejoin="round" />
        <path d="M8.7 12h5.2M8.7 15h3.4" stroke-linecap="round" />
      </svg>
    </span>
    <div class="result-file-copy">
      <strong class="result-file-name" :title="file.name">{{ file.name }}</strong>
      <p class="result-file-meta">
        <span class="result-file-tag">{{ statusLabel }}</span>
        <span>DOCX</span>
        <template v-if="file.size > 0">
          <span class="result-file-dot" aria-hidden="true">·</span>
          <span>{{ formatSize(file.size) }}</span>
        </template>
        <span v-if="!available" class="result-file-missing">此文件已删除或不可用</span>
      </p>
    </div>
    <div v-if="available" class="task-actions result-file-actions">
      <el-tooltip content="下载文件" placement="top" :show-after="400">
        <el-button
          tag="a"
          class="result-icon download"
          text
          circle
          :href="downloadUrl(file.id)"
          :aria-label="`下载 ${file.name}`"
          data-testid="download-file"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
            <path d="M12 4v11" stroke-linecap="round" />
            <path d="m7.4 10.6 4.6 4.6 4.6-4.6" stroke-linecap="round" />
            <path d="M5 19.2h14" stroke-linecap="round" />
          </svg>
        </el-button>
      </el-tooltip>
      <el-tooltip content="查看文件" placement="top" :show-after="400">
        <el-button
          class="result-icon view"
          text
          circle
          :aria-label="`查看 ${file.name}`"
          data-testid="view-file"
          @click="emit('view', file)"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
            <path
              d="M2.8 12s3.4-5.5 9.2-5.5S21.2 12 21.2 12s-3.4 5.5-9.2 5.5S2.8 12 2.8 12Z"
              stroke-linejoin="round"
            />
            <circle cx="12" cy="12" r="2.4" />
          </svg>
        </el-button>
      </el-tooltip>
    </div>
    <span v-else class="result-file-unavailable">已删除</span>
  </article>

  <div v-if="changes.length" class="change-section">
    <button
      type="button"
      class="change-toggle"
      :aria-expanded="changesOpen"
      data-testid="change-toggle"
      @click="toggleChanges"
    >
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">
        <path d="M4 7h16M4 12h16M4 17h10" stroke-linecap="round" />
      </svg>
      <span>修改详情</span>
      <span class="change-count">{{ changes.length }} 处</span>
      <svg
        class="change-chevron"
        :class="{ open: changesOpen }"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
        aria-hidden="true"
      >
        <path d="m7.5 10 4.5 4.5 4.5-4.5" stroke-linecap="round" stroke-linejoin="round" />
      </svg>
    </button>
    <div v-show="changesOpen" ref="changeList" class="change-list" data-testid="change-list">
      <section
        v-for="(change, index) in changes"
        :key="`${change.block_id}-${index}`"
        class="change-card"
        data-testid="change-card"
      >
        <header class="change-head">
          <span class="change-index">{{ index + 1 }}</span>
          <span>{{ change.location || "正文" }}</span>
        </header>
        <div class="change-grid">
          <div class="change-before">
            <span>原文</span>
            <p>{{ change.before || "（新增）" }}</p>
          </div>
          <div class="change-after">
            <span>修改后</span>
            <p>{{ change.after || "（已删除或清空）" }}</p>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>
<script setup lang="ts">
import { computed, nextTick, ref } from "vue";
import { ElButton, ElTooltip } from "element-plus";
import { downloadUrl, formatSize, type FileInfo, type TaskChange } from "../api";

const props = defineProps<{
  file: FileInfo;
  kind: "create" | "edit";
  changes?: TaskChange[];
  renamed?: boolean;
}>();
const emit = defineEmits<{ view: [file: FileInfo] }>();
const changesOpen = ref(false);
const changeList = ref<HTMLElement>();
const changes = computed(() => props.changes ?? []);
async function toggleChanges() {
  changesOpen.value = !changesOpen.value;
  if (!changesOpen.value) return;
  await nextTick();
  changeList.value?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
}
const available = computed(() => props.file.available !== false);
const statusLabel = computed(() => {
  if (props.kind === "create") return "已生成";
  if (changes.value.length) return `已修改 ${changes.value.length} 处`;
  return props.renamed ? "已重命名" : "已修改";
});
</script>
