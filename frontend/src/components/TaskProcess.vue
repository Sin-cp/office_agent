<template>
  <div class="process-block">
    <header class="assistant-head">
      <BrandMark class="assistant-avatar" />
      <div class="assistant-meta">
        <span class="assistant-name">OfficeAgent</span>
        <span class="assistant-sep" aria-hidden="true">·</span>
        <span class="assistant-status" :class="statusTone" aria-live="polite">
          <span class="status-dot" aria-hidden="true"></span>
          <span>{{ statusLabel }}</span>
        </span>
        <span class="assistant-sep" aria-hidden="true">·</span>
        <span class="assistant-elapsed">用时 {{ elapsed }} 秒</span>
      </div>
      <button
        type="button"
        class="process-toggle"
        :aria-expanded="expanded"
        aria-label="查看处理过程"
        data-testid="process-toggle"
        @click="expanded = !expanded"
      >
        <span>{{ expanded ? "收起过程" : "查看过程" }}</span>
        <svg
          class="process-chevron"
          :class="{ open: expanded }"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          aria-hidden="true"
        >
          <path d="m7.5 10 4.5 4.5 4.5-4.5" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </button>
    </header>

    <div
      v-if="hasTools"
      class="tool-timeline"
      role="log"
      aria-live="polite"
      data-testid="tool-timeline"
    >
      <template v-if="showToolRows">
        <div
          v-for="event in visibleTools"
          :key="event.id"
          class="tool-row"
          :data-state="event.status"
          data-testid="tool-row"
        >
          <span class="tool-icon" :class="event.status" aria-hidden="true">
            <svg
              v-if="event.status === 'ok'"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2.4"
            >
              <path d="m5.5 12.5 4.2 4.2 8.8-9" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
            <svg
              v-else-if="event.status === 'error'"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2.4"
            >
              <path d="m7 7 10 10M17 7 7 17" stroke-linecap="round" />
            </svg>
            <span v-else class="tool-pulse" />
          </span>
          <span class="tool-label">{{ event.label }}</span>
          <span v-if="event.summary && event.status !== 'running'" class="tool-note">{{
            event.summary
          }}</span>
          <span class="tool-time">{{ toolDuration(event) }}</span>
        </div>
        <p v-if="hiddenTools > 0" class="tool-more">
          更早的 {{ hiddenTools }} 步已折叠
        </p>
      </template>
      <p v-else class="tool-collapsed" data-testid="tool-summary">
        <span class="tool-collapsed-check" aria-hidden="true">✓</span>
        {{ toolSummary }}
      </p>
    </div>

    <div v-if="!hasTools && task.status !== 'completed'" class="process-summary">
      <div
        class="process-meter"
        role="progressbar"
        :aria-valuemin="0"
        :aria-valuemax="steps.length"
        :aria-valuenow="completedCount"
        :aria-label="`处理进度 ${completedCount}/${steps.length}`"
      >
        <span
          v-for="(_, index) in steps"
          :key="index"
          class="process-segment"
          :class="segmentState(index)"
        />
      </div>
      <div class="process-current">
        <span class="process-position">{{ currentPosition }}/{{ steps.length }}</span>
        <span class="process-label">{{ currentLabel }}</span>
      </div>
    </div>

    <p
      v-if="!hasTools && liveMessages.length"
      class="process-live"
      role="status"
      aria-live="polite"
    >
      <span class="live-indicator" aria-hidden="true"></span>
      <span>{{ liveMessages.join("；") }}…</span>
    </p>

    <el-collapse-transition>
      <div v-if="expanded && !hasTools" class="process-detail" data-testid="process-detail">
        <ol>
          <li
            v-for="(step, index) in steps"
            :key="step"
            class="process-step"
            :data-state="stepState(index)"
            data-testid="process-step"
          >
            <span class="step-marker" :class="stepState(index)">
              <svg
                v-if="stepState(index) === 'done'"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
                aria-hidden="true"
              >
                <path d="m5.5 12.5 4.2 4.2 8.8-9" stroke-linecap="round" stroke-linejoin="round" />
              </svg>
              <svg
                v-else-if="stepState(index) === 'failed'"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
                aria-hidden="true"
              >
                <path d="m7 7 10 10M17 7 7 17" stroke-linecap="round" />
              </svg>
              <span
                v-else-if="stepState(index) === 'active'"
                class="step-active-dot"
                aria-hidden="true"
              />
              <svg
                v-else-if="stepState(index) === 'stopped'"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
                aria-hidden="true"
              >
                <path d="M6 12h12" stroke-linecap="round" />
              </svg>
              <span v-else>{{ index + 1 }}</span>
            </span>
            <span class="step-title">{{ step }}</span>
            <span class="step-state">{{ stateLabel(stepState(index)) }}</span>
          </li>
        </ol>
      </div>
    </el-collapse-transition>

    <div v-if="task.status === 'failed'" class="process-error" role="alert">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true">
        <path d="M12 4.5 21 20H3Z" stroke-linejoin="round" />
        <path d="M12 9v5M12 17.2v.1" stroke-linecap="round" />
      </svg>
      <div>
        <strong>失败阶段：{{ failedStep }}</strong>
        <p>{{ task.error || "处理失败，请重试。" }}</p>
      </div>
    </div>
    <div v-else-if="task.status === 'cancelled'" class="process-note">
      <span class="note-dot" aria-hidden="true"></span>
      <span>处理已停止，未发布文件结果，已有文件保持不变。</span>
    </div>
  </div>
</template>
<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { ElCollapseTransition } from "element-plus";
import BrandMark from "./BrandMark.vue";
import type { TaskState, ToolEvent } from "../api";

type StepState = "done" | "active" | "wait" | "failed" | "stopped";
type StatusTone = "running" | "waiting" | "success" | "error" | "muted";

const props = defineProps<{ task: TaskState }>();

const steps = computed(() => props.task.steps ?? []);
const stage = computed(() =>
  Math.min(
    Math.max(props.task.stage ?? 0, 0),
    Math.max(steps.value.length - 1, 0),
  ),
);
const isActive = computed(() =>
  ["queued", "running", "cancelling"].includes(props.task.status),
);
const isFailed = computed(() => props.task.status === "failed");
const expanded = ref(isFailed.value);
const toolEvents = computed(() => props.task.tools ?? []);
const hasTools = computed(() => toolEvents.value.length > 0);
const showToolRows = computed(() => isActive.value || expanded.value);
const visibleTools = computed(() =>
  isActive.value ? toolEvents.value.slice(-5) : toolEvents.value,
);
const hiddenTools = computed(() =>
  isActive.value ? Math.max(0, toolEvents.value.length - 5) : 0,
);
const toolSummary = computed(() => {
  const failed = toolEvents.value.filter((event) => event.status === "error").length;
  return (
    `已完成 ${toolEvents.value.length} 步` +
    (failed ? ` · ${failed} 步跳过` : "")
  );
});
function toolDuration(event: ToolEvent) {
  if (event.status === "running" || !event.ended_at) return "…";
  return `${Math.max(0, event.ended_at - event.started_at).toFixed(1)}s`;
}

watch(
  () => props.task.status,
  (next, previous) => {
    if (next === "failed") expanded.value = true;
    else if (
      next === "completed" &&
      previous &&
      ["queued", "running", "cancelling"].includes(previous)
    )
      expanded.value = false;
  },
);

const statusLabel = computed(() => {
  if (props.task.status === "completed") {
    const result = props.task.result;
    if (result?.kind === "create") return "文档已生成";
    if (result?.kind === "answer") return "已回答";
    if (result?.kind === "clarify") return "需要确认";
    if (result?.kind === "edit") return "修改完成";
    if (result?.file || result?.outputs?.length) return "修改完成";
    return "已回答";
  }
  return {
    queued: "等待处理",
    running: "正在处理",
    cancelling: "正在停止",
    failed: "处理失败",
    cancelled: "已停止",
  }[props.task.status];
});

const statusTone = computed<StatusTone>(() => {
  if (props.task.status === "completed")
    return props.task.result?.kind === "clarify" ? "waiting" : "success";
  if (props.task.status === "failed") return "error";
  if (props.task.status === "cancelled") return "muted";
  if (props.task.status === "running") return "running";
  return "waiting";
});

const elapsed = computed(() => {
  const value = props.task.elapsed_seconds;
  return (Number.isFinite(value) ? Math.max(0, value) : 0).toFixed(1);
});

const currentPosition = computed(() => {
  if (props.task.status === "queued") return 0;
  if (props.task.status === "completed") return steps.value.length;
  return Math.min(stage.value + 1, steps.value.length);
});

const completedCount = computed(() => {
  if (props.task.status === "completed") return steps.value.length;
  if (props.task.status === "queued") return 0;
  return Math.min(stage.value, steps.value.length);
});

const currentLabel = computed(() => {
  if (props.task.status === "queued") return "等待开始";
  if (props.task.status === "completed") return "全部完成";
  return steps.value[stage.value] || (isFailed.value ? "处理中断" : "正在处理");
});

const failedStep = computed(
  () => steps.value[stage.value] || "处理阶段",
);

const liveMessages = computed(() =>
  isActive.value
    ? [props.task.web_activity, props.task.activity].filter(
        (item): item is string => !!item,
      )
    : [],
);

function stepState(index: number): StepState {
  const status = props.task.status;
  if (status === "completed") return "done";
  if (status === "failed")
    return index < stage.value
      ? "done"
      : index === stage.value
        ? "failed"
        : "wait";
  if (status === "cancelled")
    return index < stage.value
      ? "done"
      : index === stage.value
        ? "stopped"
        : "wait";
  if (status === "queued") return "wait";
  if (index < stage.value) return "done";
  if (index === stage.value)
    return status === "cancelling" ? "stopped" : "active";
  return "wait";
}

function segmentState(index: number) {
  const state = stepState(index);
  return state === "wait" ? "wait" : state;
}

function stateLabel(state: StepState) {
  return {
    done: "已完成",
    active: "进行中",
    wait: "等待",
    failed: "失败",
    stopped: "已停止",
  }[state];
}
</script>
