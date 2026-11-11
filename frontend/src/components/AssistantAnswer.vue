<template>
  <div ref="rootEl" class="assistant-content" data-testid="assistant-answer">
    <div class="answer-reveal" :class="{ 'is-typing': typing }">
      <RichText v-if="visibleText" :text="visibleText" :caret="typing" />
    </div>

    <div
      v-if="!typing"
      class="answer-extras"
      :class="{ 'is-fresh': animated }"
    >
      <p
        v-for="notice in notices"
        :key="notice.code"
        class="web-notice"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true">
          <circle cx="12" cy="12" r="8.5" />
          <path d="M12 7.5v5.2M12 16.2v.1" stroke-linecap="round" />
        </svg>
        <span>{{ notice.message }}</span>
      </p>

      <div v-if="outputs.length" class="result-files">
        <ResultFileCard
          v-for="output in outputs"
          :key="output.file.id"
          :file="output.file"
          :kind="output.kind ?? fileKind"
          :changes="output.changes"
          :renamed="output.renamed ?? result?.renamed"
          @view="emit('view', $event)"
        />
      </div>

      <section v-if="localEdit" class="local-file" data-testid="local-file">
        <div class="local-file-copy">
          <span class="local-file-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7">
              <path
                d="M3.5 7.6a2 2 0 0 1 2-2h3.7l2 2.3h7.3a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2Z"
                stroke-linejoin="round"
              />
            </svg>
          </span>
          <div>
            <p class="local-file-title">
              {{ localEdit.overwritten ? "已覆盖源文件" : "原文件未改动" }}
            </p>
            <p class="local-file-path" :title="localEdit.path">
              {{ localEdit.path }}
            </p>
            <p v-if="localEdit.overwritten" class="local-file-backup">
              备份：{{ localEdit.overwritten.backup }}
            </p>
          </div>
        </div>
        <el-button
          v-if="!localEdit.overwritten"
          class="local-file-button"
          size="small"
          :disabled="task.status !== 'completed'"
          data-testid="overwrite-local"
          @click="emit('overwrite')"
          >覆盖源文件…</el-button
        >
      </section>

      <section v-if="sources.length" class="sources">
        <button
          type="button"
          class="sources-toggle"
          :aria-expanded="sourcesOpen"
          data-testid="sources-toggle"
          @click="toggleSources"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true">
            <path d="M10 13.5a4 4 0 0 0 5.7 0l2.1-2.1a4 4 0 0 0-5.7-5.7L10.8 7" stroke-linecap="round" />
            <path d="M14 10.5a4 4 0 0 0-5.7 0l-2.1 2.1a4 4 0 0 0 5.7 5.7l1.3-1.3" stroke-linecap="round" />
          </svg>
          <span>参考来源</span>
          <span class="sources-count">{{ sources.length }}</span>
          <svg
            class="sources-chevron"
            :class="{ open: sourcesOpen }"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            aria-hidden="true"
          >
            <path d="m7.5 10 4.5 4.5 4.5-4.5" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
        </button>
        <div
          v-show="sourcesOpen"
          ref="sourcesList"
          class="web-sources sources-list"
          aria-label="查询来源"
        >
          <ol>
            <li v-for="(source, index) in sources" :key="source.url">
              <span class="source-index" aria-hidden="true">{{ index + 1 }}</span>
              <div class="source-copy">
                <a
                  v-if="safeSource(source.url)"
                  :href="source.url"
                  target="_blank"
                  rel="noopener noreferrer"
                  referrerpolicy="no-referrer"
                  >{{ source.title }}</a
                >
                <span v-else>{{ source.title }}</span>
                <small>
                  {{ source.kind === "search" ? "搜索摘要" : "网页正文" }}
                  <template v-if="domain(source.url)"> · {{ domain(source.url) }}</template>
                </small>
              </div>
            </li>
          </ol>
        </div>
      </section>
    </div>
  </div>
</template>
<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import { ElButton } from "element-plus";
import ResultFileCard from "./ResultFileCard.vue";
import RichText from "./RichText.vue";
import type {
  FileInfo,
  TaskChange,
  TaskState,
  WebSource,
} from "../api";

interface Output {
  file: FileInfo;
  changes: TaskChange[];
  kind?: "create" | "edit";
  renamed?: boolean;
}

const props = withDefaults(
  defineProps<{ task: TaskState; reveal?: boolean }>(),
  { reveal: false },
);
const emit = defineEmits<{
  view: [file: FileInfo];
  settled: [];
  overwrite: [];
}>();
const sourcesOpen = ref(false);
const sourcesList = ref<HTMLElement>();
async function toggleSources() {
  sourcesOpen.value = !sourcesOpen.value;
  if (!sourcesOpen.value) return;
  await nextTick();
  sourcesList.value?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
}

const result = computed(() => props.task.result);
const localEdit = computed(() => result.value?.local_edit);
const notices = computed(() => result.value?.web_notices ?? []);
const sources = computed<WebSource[]>(() => result.value?.web_sources ?? []);
const fileKind = computed<"create" | "edit">(() =>
  result.value?.kind === "create" ? "create" : "edit",
);

const outputs = computed<Output[]>(() => {
  const value = result.value;
  if (!value) return [];
  if (value.outputs?.length) {
    return value.outputs.map((output) => ({
      file: output.file,
      changes: output.changes ?? [],
      kind: output.kind,
      renamed: output.renamed,
    }));
  }
  if (!value.file) return [];
  const changes =
    value.changes?.length
      ? value.changes
      : value.before !== undefined || value.after !== undefined
        ? [
            {
              op: "replace",
              block_id: "",
              location: "正文",
              before: value.before ?? "",
              after: value.after ?? "",
            },
          ]
        : [];
  return [{ file: value.file, changes }];
});

/* Answers arrive in one piece from the task API, so the finished text is
   revealed like a live answer: characters land over ~1-2s instead of the whole
   paragraph popping in. History items (already complete on mount) skip it. */
const rootEl = ref<HTMLElement>();
const REVEAL_MIN_MS = 700;
const REVEAL_MAX_MS = 2000;
const REVEAL_MS_PER_CHAR = 11;
const REVEAL_DELAY_MS = 120;

const fullText = computed(() => result.value?.text ?? "");
const characters = computed(() => Array.from(fullText.value));
const reducedMotion =
  typeof window !== "undefined" &&
  window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches === true;
const animated = props.reveal && !reducedMotion;
const typed = ref(animated ? 0 : characters.value.length);
const typing = computed(() => typed.value < characters.value.length);
/**
 * A half-typed `**` or backtick would show up as literal syntax, so drop a
 * dangling opener and close whatever emphasis/code span is still open: partial
 * bold text renders bold instead of flashing `**第二天`.
 */
function settleMarkers(source: string) {
  const text = source.replace(/(\*\*|`|\*)$/, "");
  const bold = (text.match(/\*\*/g) || []).length % 2 === 1;
  const code = (text.match(/`/g) || []).length % 2 === 1;
  return text + (bold ? "**" : "") + (code ? "`" : "");
}
const visibleText = computed(() => {
  if (!typing.value) return fullText.value;
  return settleMarkers(characters.value.slice(0, typed.value).join(""));
});

let ticker: ReturnType<typeof setInterval> | undefined;
let delay: ReturnType<typeof setTimeout> | undefined;
const TICK_MS = 16;

function stop() {
  clearInterval(ticker);
  clearTimeout(delay);
  ticker = undefined;
  delay = undefined;
}

function scroller() {
  return (rootEl.value?.closest(".conversation-history") as HTMLElement | null) ?? null;
}
function atBottom(area: HTMLElement) {
  return area.scrollHeight - area.scrollTop - area.clientHeight < 120;
}

function playReveal() {
  const total = characters.value.length;
  if (!animated || !total) {
    emit("settled");
    return;
  }
  const duration = Math.min(
    REVEAL_MAX_MS,
    Math.max(REVEAL_MIN_MS, total * REVEAL_MS_PER_CHAR),
  );
  const area = scroller();
  let follow = !!area;
  let elapsed = 0;
  const pin = () => {
    if (!area || !follow) return;
    void nextTick(() => {
      area.scrollTop = area.scrollHeight;
    });
  };
  const finish = () => {
    stop();
    typed.value = total;
    pin();
    emit("settled");
  };
  // Step on a timer instead of a wall clock so the pacing is identical for
  // every frame budget: ~60 steps a second live, one step per tick in tests.
  delay = setTimeout(() => {
    ticker = setInterval(() => {
      if (area && follow) follow = atBottom(area);
      elapsed += TICK_MS;
      const progress = Math.min(1, elapsed / duration);
      typed.value = Math.floor(progress * total);
      pin();
      if (progress >= 1) finish();
    }, TICK_MS);
  }, REVEAL_DELAY_MS);
}

onMounted(playReveal);
onBeforeUnmount(stop);

function safeSource(url: string) {
  try {
    const parsed = new URL(url);
    return (
      ["http:", "https:"].includes(parsed.protocol) &&
      !parsed.username &&
      !parsed.password
    );
  } catch {
    return false;
  }
}

function domain(url: string) {
  try {
    return new URL(url).hostname;
  } catch {
    return "";
  }
}
</script>
