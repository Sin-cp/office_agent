<template>
  <div
    class="mention-popover"
    role="dialog"
    aria-label="插入项目文档"
  >
    <header class="mention-head">
      <div class="mention-head-copy">
        <div class="mention-title-row">
          <span class="mention-title">项目文件</span>
          <span class="mention-count">{{ total }}</span>
          <span v-if="query" class="mention-filter"
            >筛选“{{ query }}”</span
          >
        </div>
        <p>勾选文档会插入 @ 提及，并加入本轮附件</p>
      </div>
      <button
        type="button"
        class="mention-close"
        aria-label="关闭文档选择"
        @mousedown.prevent
        @click="emit('close')"
      >
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="1.8"
          aria-hidden="true"
        >
          <path d="m6.5 6.5 11 11m0-11-11 11" stroke-linecap="round" />
        </svg>
      </button>
    </header>

    <div
      id="mention-listbox"
      ref="list"
      class="mention-scroll"
      role="listbox"
      aria-label="项目文档"
      :aria-activedescendant="
        items.length ? optionId(activeIndex) : undefined
      "
    >
      <p v-if="error && items.length" class="mention-inline-error" role="alert">
        {{ error }}
      </p>

      <div v-if="loading && !items.length" class="mention-loading">
        <el-skeleton :rows="4" animated />
      </div>

      <div v-else-if="error && !items.length" class="mention-state">
        <span class="mention-state-icon" aria-hidden="true">
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="1.7"
          >
            <circle cx="12" cy="12" r="8.4" />
            <path d="M12 7.8v5.1m0 3.1v.2" stroke-linecap="round" />
          </svg>
        </span>
        <p>{{ error }}</p>
        <el-button class="mention-retry" link @click="emit('retry')"
          >重新加载</el-button
        >
      </div>

      <div v-else-if="!items.length" class="mention-state">
        <span class="mention-state-icon" aria-hidden="true">
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="1.6"
          >
            <path
              d="M3.5 7.6a2 2 0 0 1 2-2h3.7l2 2.3h7.3a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2Z"
              stroke-linejoin="round"
            />
          </svg>
        </span>
        <p v-if="query">没有匹配“{{ query }}”的文档</p>
        <p v-else>
          当前会话还没有文档
          <span class="mention-state-sub">先上传 DOCX，或发送消息生成文档</span>
        </p>
      </div>

      <ul v-else class="mention-list">
        <li
          v-for="(row, index) in items"
          :id="optionId(index)"
          :key="row.id"
          class="mention-row"
          :class="{
            'is-active': index === activeIndex,
            'is-selected': attached(row),
            'is-mentioned': mentioned(row),
            'is-unavailable': unavailable(row),
          }"
          role="option"
          :aria-selected="attached(row)"
          :aria-disabled="unavailable(row) || undefined"
          :data-testid="`mention-row-${row.id}`"
          @mousedown.prevent
          @mouseenter="emit('hover', index)"
          @click="choose(row)"
        >
          <button
            type="button"
            class="mention-check"
            :class="{ 'is-checked': attached(row) }"
            :disabled="unavailable(row)"
            :aria-label="
              attached(row) ? `取消选择 ${displayName(row)}` : `选择 ${displayName(row)}`
            "
            :aria-pressed="attached(row)"
            :data-testid="`mention-toggle-${row.id}`"
            @click.stop="emit('toggle', row)"
          >
            <svg
              v-if="attached(row)"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2.4"
              aria-hidden="true"
            >
              <path d="m5.5 12.5 4.2 4.2 8.8-9.4" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
          </button>

          <span class="mention-kind" :class="{ generated: row.origin === 'generated' }" aria-hidden="true">
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

          <span class="mention-copy">
            <span class="mention-name-line">
              <span class="mention-name" :title="displayName(row)">{{
                displayName(row)
              }}</span>
              <span v-if="mentioned(row)" class="mention-mentioned">@ 已插入</span>
            </span>
            <span class="mention-meta">
              <span class="mention-origin">{{
                row.origin === "generated" ? "生成" : "上传"
              }}</span>
              <span v-if="bytes(row) != null">{{ formatSize(bytes(row)!) }}</span>
              <span v-if="date(row)">{{ date(row) }}</span>
              <span v-if="unavailable(row)" class="mention-unavailable"
                >文件不可用</span
              >
            </span>
          </span>

          <span
            v-if="index === activeIndex && !unavailable(row)"
            class="mention-enter"
            aria-hidden="true"
            >↵</span
          >
        </li>
      </ul>

      <p v-if="loading && items.length" class="mention-refreshing" role="status">
        正在刷新项目文件…
      </p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from "vue";
import { ElButton, ElSkeleton } from "element-plus";
import { formatSize, type SourceFile } from "../api";

const props = defineProps<{
  items: SourceFile[];
  total: number;
  selectedIds: string[];
  mentionedIds: string[];
  activeIndex: number;
  query: string;
  loading?: boolean;
  error?: string;
}>();
const emit = defineEmits<{
  choose: [row: SourceFile];
  toggle: [row: SourceFile];
  hover: [index: number];
  close: [];
  retry: [];
}>();

const list = ref<HTMLElement>();

function currentFile(row: SourceFile) {
  return row.current_file;
}
function displayName(row: SourceFile) {
  return currentFile(row)?.name || row.name || "未命名文档";
}
function unavailable(row: SourceFile) {
  return !row.current_file || row.current_file.available === false;
}
function attached(row: SourceFile) {
  const file = currentFile(row);
  return !!file && props.selectedIds.includes(file.id);
}
function mentioned(row: SourceFile) {
  const file = currentFile(row);
  return !!file && props.mentionedIds.includes(file.id);
}
function bytes(row: SourceFile) {
  return currentFile(row)?.size ?? row.size;
}
function date(row: SourceFile) {
  const value = currentFile(row)?.created_at || row.created_at;
  if (!value) return "";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "";
  return parsed.toLocaleString("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
function optionId(index: number) {
  return `mention-option-${index}`;
}
function choose(row: SourceFile) {
  if (!unavailable(row)) emit("choose", row);
}

function scrollActive() {
  void nextTick(() => {
    const element = list.value?.querySelector<HTMLElement>(
      ".mention-row.is-active",
    );
    element?.scrollIntoView?.({ block: "nearest" });
  });
}
watch(() => props.activeIndex, scrollActive);
watch(() => props.items, scrollActive);
onMounted(scrollActive);
</script>
