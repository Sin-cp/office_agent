<template>
  <div
    class="turn-actions"
    :class="`is-${variant}`"
    role="group"
    :aria-label="variant === 'prompt' ? '提示词操作' : '回答操作'"
  >
    <el-tooltip
      v-for="action in actions"
      :key="action"
      :content="labels[action]"
      placement="top"
      :show-after="400"
    >
      <el-button
        class="result-icon turn-action"
        :class="action"
        text
        circle
        :disabled="disabled || (action === 'copy' && !copyText)"
        :aria-label="labels[action]"
        :data-testid="`turn-${action}`"
        @click="run(action)"
      >
        <svg
          v-if="action === 'copy'"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="1.7"
        >
          <rect x="8.6" y="8.6" width="10.4" height="10.4" rx="2.2" />
          <path d="M15.4 5.6H6.6a2 2 0 0 0-2 2v8.8" stroke-linecap="round" />
        </svg>
        <svg
          v-else-if="action === 'regenerate'"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="1.7"
        >
          <path d="M19.2 12a7.2 7.2 0 1 1-2.5-5.5" stroke-linecap="round" />
          <path d="M19.6 4.4v4.2h-4.2" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
        <svg
          v-else
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="1.7"
        >
          <path d="M5.6 7.2h12.8" stroke-linecap="round" />
          <path d="M9.6 7.2V5.6a1.3 1.3 0 0 1 1.3-1.3h2.2a1.3 1.3 0 0 1 1.3 1.3v1.6" stroke-linejoin="round" />
          <path d="M7.2 7.2l.8 11a1.6 1.6 0 0 0 1.6 1.5h4.8a1.6 1.6 0 0 0 1.6-1.5l.8-11" stroke-linejoin="round" />
          <path d="M10.6 11v5.2M13.4 11v5.2" stroke-linecap="round" />
        </svg>
      </el-button>
    </el-tooltip>
    <span v-if="feedback" class="turn-feedback" role="status">{{ feedback }}</span>
  </div>
</template>
<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from "vue";
import { ElButton, ElTooltip } from "element-plus";
import { copyText as writeClipboard } from "../utils/clipboard";

type TurnActionKey = "copy" | "regenerate" | "delete";

const props = withDefaults(
  defineProps<{
    actions: TurnActionKey[];
    variant?: "prompt" | "answer";
    copyText?: string;
    disabled?: boolean;
  }>(),
  { variant: "answer", copyText: "", disabled: false },
);
const emit = defineEmits<{ select: [key: "regenerate" | "delete"] }>();

const labels = computed<Record<TurnActionKey, string>>(() => ({
  copy: props.variant === "prompt" ? "复制提示词" : "复制回答",
  regenerate: "重新生成",
  delete: "删除这一轮对话",
}));

const feedback = ref("");
let timer: ReturnType<typeof setTimeout> | undefined;
function flash(message: string) {
  feedback.value = message;
  clearTimeout(timer);
  timer = setTimeout(() => {
    feedback.value = "";
  }, 1600);
}

async function run(action: TurnActionKey) {
  if (action === "copy") {
    flash((await writeClipboard(props.copyText)) ? "已复制" : "复制失败");
    return;
  }
  emit("select", action);
}

onBeforeUnmount(() => clearTimeout(timer));
</script>
