<template>
  <section
    ref="composerEl"
    class="composer"
    :class="{ dragging: dragDepth > 0, 'is-mentioning': mentionOpen }"
    aria-label="文档编辑输入框"
    @dragenter.prevent="dragDepth++"
    @dragleave.prevent="dragDepth = Math.max(0, dragDepth - 1)"
    @dragover.prevent
    @drop.prevent="dropped"
    @paste="pasted"
  >
    <FileMentionMenu
      v-if="mentionOpen"
      :items="filteredSourceFiles"
      :total="sourceFileCount"
      :selected-ids="attachedIds"
      :mentioned-ids="mentionedIds"
      :active-index="activeIndex"
      :query="mentionQuery"
      :loading="sourcesLoading"
      :error="sourcesError"
      @choose="chooseSource"
      @toggle="toggleSource"
      @hover="activeIndex = $event"
      @close="closeMention"
      @retry="emit('refresh-sources')"
    />
    <div v-if="images && images.length" class="composer-images">
      <div
        v-for="image in images"
        :key="image.id"
        class="selected-image"
        :title="image.name"
      >
        <el-image
          class="selected-image-preview"
          :src="imageUrl(image.id)"
          :preview-src-list="[imageUrl(image.id)]"
          preview-teleported
          fit="cover"
          :alt="image.name"
        />
        <el-button
          class="image-remove"
          text
          circle
          aria-label="移除本轮图片"
          :disabled="busy"
          @click="emit('remove-image', image.id)"
          >×</el-button
        >
      </div>
    </div>
    <div v-if="attachedFiles.length" class="composer-files">
      <div
        v-for="file in attachedFiles"
        :key="file.id"
        class="selected-file"
      >
        <span class="selected-file-icon" aria-hidden="true">W</span>
        <span class="selected-file-name" :title="file.name">{{ file.name }}</span>
        <el-button
          class="icon-button"
          text
          circle
          aria-label="清除当前选中文件"
          title="取消选择，不删除已上传文件"
          :disabled="busy"
          @click="removeFile(file)"
          >×</el-button
        >
      </div>
    </div>
    <div
      ref="editorEl"
      class="composer-editor"
      :class="{ 'is-composing': composing }"
      @click="editorClick"
    >
      <div ref="highlightRef" class="composer-highlight" aria-hidden="true">
        <span
          v-for="(segment, index) in highlightSegments"
          :key="index"
          :class="{
            'mention-token': segment.mention,
            'composing-token': segment.composing,
          }"
          >{{ segment.text }}</span
        ><span v-if="overlayText.endsWith('\n')" class="mention-tail">&#8203;</span>
      </div>
      <el-input
        ref="inputRef"
        v-model="draft"
        class="composer-input"
        type="textarea"
        aria-label="输入文档问题或修改要求"
        aria-haspopup="listbox"
        aria-autocomplete="list"
        :aria-expanded="mentionOpen"
        :aria-controls="mentionOpen ? 'mention-listbox' : undefined"
        :aria-activedescendant="
          mentionOpen && filteredSourceFiles.length
            ? `mention-option-${activeIndex}`
            : undefined
        "
        placeholder="聊点什么，输入 @ 引用文档，或上传文件进行总结、提问和修改…"
        :autosize="{ minRows: conversation ? 2 : 3, maxRows: 8 }"
        :disabled="busy"
        :maxlength="2000"
        title="Enter 发送，Shift + Enter 换行；输入 @ 可直接引用项目文档"
        @input="handleInput"
        @keydown="keydown"
        @compositionstart="compositionStart"
        @compositionupdate="compositionUpdate"
        @compositionend="compositionEnd"
      />
    </div>
    <div class="composer-toolbar">
      <input
        ref="picker"
        class="visually-hidden"
        type="file"
        multiple
        accept=".docx,image/png,image/jpeg,image/gif,image/webp"
        tabindex="-1"
        aria-label="选择 DOCX 文件"
        :disabled="busy"
        @change="picked"
      />
      <el-tooltip content="上传 DOCX 文件" placement="top" :show-after="400">
        <el-button
          class="icon-button attach"
          text
          circle
          aria-label="上传 DOCX 文件"
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
        </el-button>
      </el-tooltip>
      <span class="upload-hint">{{
        busy ? "正在处理…" : "支持多份 .docx 与图片 · 单图最大 5 MiB"
      }}</span>
      <span
        class="web-switch-row"
        :title="
          webEnabled !== false
            ? '需要实时信息时自动搜索，也可读取网页链接'
            : '联网已关闭，本轮不会搜索或读取网页'
        "
      >
        <el-switch
          :model-value="webEnabled !== false"
          aria-label="联网"
          :disabled="busy"
          data-testid="web-switch"
          @update:model-value="emit('update:webEnabled', $event === true)"
        />
        <span>联网</span>
      </span>
      <template v-if="projectFilesAvailable">
        <span class="composer-divider" aria-hidden="true"></span>
        <el-button
          class="project-files-button"
          title="查看项目文件并选择本轮附件"
          data-testid="project-files"
          @click="projectFilesAction"
        >
          <span class="project-files-icon" aria-hidden="true">
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.7"
            >
              <path
                d="M3.5 7.6a2 2 0 0 1 2-2h3.7l2 2.3h7.3a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2Z"
                stroke-linejoin="round"
              />
            </svg>
          </span>
          <span class="project-files-text">
            <span class="project-files-label">项目文件</span>
            <span class="project-files-sep" aria-hidden="true"></span>
            <span class="project-files-sub">选择本轮附件</span>
          </span>
          <svg
            class="project-files-chevron"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            aria-hidden="true"
          >
            <path d="m9.5 6.5 5.5 5.5-5.5 5.5" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
        </el-button>
      </template>
      <span class="ai-hint"></span>
      <el-button
        class="send-button"
        :class="{ 'is-stop': showStop, 'is-stopping': stopDisabled }"
        circle
        type="primary"
        :disabled="showStop ? stopDisabled : !canSend"
        :aria-label="
          showStop
            ? stopDisabled
              ? '正在停止'
              : '停止处理'
            : '发送处理要求'
        "
        :title="
          showStop
            ? stopDisabled
              ? '正在停止…'
              : '停止处理'
            : '发送处理要求'
        "
        @click="primaryAction"
        style="width: 36px"
      >
        <svg
          v-if="showStop"
          class="stop-glyph"
          viewBox="0 0 24 24"
          fill="currentColor"
          aria-hidden="true"
        >
          <rect x="5" y="5" width="14" height="14" rx="2.2" />
        </svg>
        <svg
          v-else
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          aria-hidden="true"
        >
          <path d="M12 19V5m-6 6 6-6 6 6" />
        </svg>
      </el-button>
    </div>
    <div v-if="dragDepth" class="drop-overlay">
      {{ busy ? "正在上传，请稍候" : "松开即可上传 DOCX 文件" }}
    </div>
  </section>
  <p v-if="error" class="feedback error" role="alert">{{ error }}</p>
  <p v-else-if="message" class="feedback" role="status">{{ message }}</p>
  <!-- <p v-else class="composer-note">{{ conversation ? "可直接聊天；仅处理本轮选中的附件" : "直接发送开始聊天，也可拖入多份 Word 文档" }}</p> -->
</template>
<script setup lang="ts">
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from "vue";
import { ElButton, ElImage, ElInput, ElSwitch, ElTooltip } from "element-plus";
import type { InputInstance } from "element-plus";
import { imageUrl, type AssetInfo, type FileInfo, type SourceFile } from "../api";
import FileMentionMenu from "./FileMentionMenu.vue";

const props = defineProps<{
  busy: boolean;
  webEnabled?: boolean;
  sendDisabled?: boolean;
  conversation?: boolean;
  message: string;
  error: string;
  file?: FileInfo | null;
  files?: FileInfo[];
  images?: AssetInfo[];
  projectId?: string | null;
  sourceFiles?: SourceFile[];
  sourcesLoading?: boolean;
  sourcesError?: string;
  projectFilesAvailable?: boolean;
  canStop?: boolean;
  stopping?: boolean;
}>();
const emit = defineEmits<{
  "update:webEnabled": [value: boolean];
  attach: [file: FileInfo];
  clear: [id: string];
  send: [];
  upload: [files: File[]];
  invalid: [message: string];
  "project-files": [];
  "refresh-sources": [];
  "upload-images": [files: File[]];
  "remove-image": [id: string];
  stop: [];
}>();

const attachedFiles = computed(
  () => props.files || (props.file ? [props.file] : []),
);
const attachedIds = computed(() => attachedFiles.value.map((file) => file.id));
const draft = defineModel<string>({ default: "" });
const inputRef = ref<InputInstance>();
const composerEl = ref<HTMLElement>();
const editorEl = ref<HTMLElement>();
const highlightRef = ref<HTMLElement>();
const composing = ref(false);
const composingText = ref<string | null>(null);
const composingRange = ref<{ start: number; end: number } | null>(null);
const canSend = computed(
  () => !props.busy && !props.sendDisabled && !!draft.value.trim(),
);
const showStop = computed(() => props.canStop === true);
const stopDisabled = computed(() => props.stopping === true);

const mentionOpen = ref(false);
const mentionQuery = ref("");
const mentionTrigger = ref<{ start: number; end: number } | null>(null);
const activeIndex = ref(0);
const sourceFileCount = computed(() => props.sourceFiles?.length || 0);

interface MentionRange {
  start: number;
  end: number;
  name: string;
}
interface HighlightSegment {
  text: string;
  mention: boolean;
  composing: boolean;
}

function editorTextarea() {
  return editorEl.value?.querySelector("textarea") ?? undefined;
}
function sourceName(row: SourceFile) {
  return row.current_file?.name || row.name || "";
}
function rowSelectable(row: SourceFile | undefined) {
  return !!row?.current_file && row.current_file.available !== false;
}
function firstSelectableIndex(rows: SourceFile[]) {
  const index = rows.findIndex((row) => rowSelectable(row));
  return index < 0 ? 0 : index;
}

function isMentionBoundary(previous: string) {
  // Keep e-mail addresses like hello@example.com from opening the picker.
  return !previous || !/[A-Za-z0-9_@.%+-]/.test(previous);
}
function mentionRangesIn(text: string, names: string[]): MentionRange[] {
  const unique = [...new Set(names.filter((name) => !!name))].sort(
    (left, right) => right.length - left.length,
  );
  if (!unique.length || !text.includes("@")) return [];
  const ranges: MentionRange[] = [];
  for (let index = 0; index < text.length; index++) {
    if (text[index] !== "@" || !isMentionBoundary(text[index - 1] || "")) {
      continue;
    }
    const name = unique.find((candidate) =>
      text.startsWith(candidate, index + 1),
    );
    if (!name) continue;
    ranges.push({ start: index, end: index + name.length + 1, name });
    index += name.length;
  }
  return ranges;
}
function hasMentionToken(text: string, name: string) {
  return !!name && mentionRangesIn(text, [name]).length > 0;
}
const overlayText = computed(() => composingText.value ?? draft.value);
const committedDraft = computed(() => {
  const trigger = mentionTrigger.value;
  if (!trigger) return draft.value;
  return (
    draft.value.slice(0, trigger.start) + draft.value.slice(trigger.end)
  );
});
const mentionedIds = computed(() => {
  const ids: string[] = [];
  for (const row of props.sourceFiles || []) {
    const file = row.current_file;
    if (file && hasMentionToken(committedDraft.value, file.name))
      ids.push(file.id);
  }
  return ids;
});
const mentionRanges = computed(() =>
  mentionRangesIn(
    overlayText.value,
    attachedFiles.value.map((file) => file.name),
  ),
);
const highlightSegments = computed<HighlightSegment[]>(() => {
  const text = overlayText.value;
  if (!text) return [];
  const composition = composingRange.value;
  const compositionStart = composition
    ? Math.max(0, Math.min(text.length, composition.start))
    : null;
  const compositionEnd = composition
    ? Math.max(0, Math.min(text.length, composition.end))
    : null;
  const points = new Set<number>([0, text.length]);
  for (const range of mentionRanges.value) {
    if (range.start > 0 && range.start < text.length) points.add(range.start);
    if (range.end > 0 && range.end < text.length) points.add(range.end);
  }
  if (
    compositionStart !== null &&
    compositionEnd !== null &&
    compositionEnd > compositionStart
  ) {
    points.add(compositionStart);
    points.add(compositionEnd);
  }
  const boundaries = [...points].sort((left, right) => left - right);
  const segments: HighlightSegment[] = [];
  for (let index = 0; index < boundaries.length - 1; index++) {
    const start = boundaries[index]!;
    const end = boundaries[index + 1]!;
    if (end <= start) continue;
    segments.push({
      text: text.slice(start, end),
      mention: mentionRanges.value.some(
        (range) => range.start === start && range.end === end,
      ),
      composing:
        compositionStart !== null &&
        compositionEnd !== null &&
        start >= compositionStart &&
        end <= compositionEnd,
    });
  }
  return segments;
});
const filteredSourceFiles = computed(() => {
  const rows = props.sourceFiles || [];
  const query = mentionQuery.value.trim().toLowerCase();
  if (!query) return rows;
  return rows.filter(
    (row) =>
      sourceName(row).toLowerCase().includes(query) ||
      row.name.toLowerCase().includes(query),
  );
});

function syncHighlightScroll() {
  const area = editorTextarea();
  const ghost = highlightRef.value;
  if (area && ghost && ghost.scrollTop !== area.scrollTop) {
    ghost.scrollTop = area.scrollTop;
  }
}
function isAttached(row: SourceFile) {
  const file = row.current_file;
  return !!file && attachedIds.value.includes(file.id);
}
function isMentioned(row: SourceFile) {
  const name = sourceName(row);
  return !!name && hasMentionToken(committedDraft.value, name);
}

function finishMentionQuery() {
  mentionTrigger.value = null;
  mentionQuery.value = "";
  activeIndex.value = firstSelectableIndex(filteredSourceFiles.value);
}
function closeMention() {
  if (!mentionOpen.value) return;
  mentionOpen.value = false;
  mentionTrigger.value = null;
  mentionQuery.value = "";
}

function openMention(start: number) {
  if (props.busy) return;
  mentionOpen.value = true;
  mentionQuery.value = "";
  mentionTrigger.value = { start, end: start + 1 };
  activeIndex.value = firstSelectableIndex(filteredSourceFiles.value);
  emit("refresh-sources");
}

let compositionAnchor = 0;
let compositionReplaceEnd = 0;
function compositionStart() {
  composing.value = true;
  composingRange.value = null;
  const area = editorTextarea();
  compositionAnchor = area?.selectionStart ?? draft.value.length;
  compositionReplaceEnd = area?.selectionEnd ?? compositionAnchor;
  composingText.value = draft.value;
}
function compositionUpdate(event: CompositionEvent) {
  if (!composing.value) return;
  const data = event.data || "";
  const base = draft.value;
  const start = Math.min(compositionAnchor, base.length);
  const end = Math.min(
    Math.max(compositionReplaceEnd, start),
    base.length,
  );
  const draftAlreadyHasComposition =
    !!data && base.slice(start, start + data.length) === data;
  composingText.value = draftAlreadyHasComposition
    ? base
    : base.slice(0, start) + data + base.slice(end);
  composingRange.value = data ? { start, end: start + data.length } : null;
  void nextTick(syncHighlightScroll);
}
function compositionEnd(event: CompositionEvent) {
  if (!composing.value) return;
  composing.value = false;
  composingRange.value = null;
  const area = editorTextarea();
  if (area) {
    composingText.value = area.value;
  } else {
    const data = event.data || "";
    const base = draft.value;
    const start = Math.min(compositionAnchor, base.length);
    const end = Math.min(
      Math.max(compositionReplaceEnd, start),
      base.length,
    );
    composingText.value = base.slice(0, start) + data + base.slice(end);
  }
  void nextTick(() => {
    if (!composing.value && draft.value === (editorTextarea()?.value ?? "")) {
      composingText.value = null;
    }
  });
}
function handleNativeInput() {
  const area = editorTextarea();
  if (!area) return;
  if (composing.value || composingText.value !== null) {
    composingText.value = area.value;
    void nextTick(syncHighlightScroll);
  }
}

function handleInput(value: string) {
  void nextTick(syncHighlightScroll);
  if (mentionOpen.value) {
    const trigger = mentionTrigger.value;
    const caret = editorTextarea()?.selectionStart ?? value.length;
    if (!trigger) {
      closeMention();
    } else if (value[trigger.start] !== "@" || caret <= trigger.start) {
      closeMention();
    } else {
      const query = value.slice(trigger.start + 1, caret);
      if (query.length > 80 || /[\s\n\r]/.test(query)) {
        closeMention();
      } else {
        mentionQuery.value = query;
        mentionTrigger.value = { start: trigger.start, end: caret };
        const rows = filteredSourceFiles.value;
        activeIndex.value = firstSelectableIndex(rows);
      }
    }
  }
  if (!mentionOpen.value) {
    const caret = editorTextarea()?.selectionStart ?? value.length;
    if (
      caret > 0 &&
      value[caret - 1] === "@" &&
      isMentionBoundary(value[caret - 2] || "")
    ) {
      openMention(caret - 1);
    }
  }
}

function moveActive(delta: number) {
  const rows = filteredSourceFiles.value;
  if (!rows.length) return;
  let index = activeIndex.value;
  for (let step = 0; step < rows.length; step++) {
    index = (index + delta + rows.length) % rows.length;
    if (rowSelectable(rows[index])) {
      activeIndex.value = index;
      return;
    }
  }
}
function chooseActive() {
  const row = filteredSourceFiles.value[activeIndex.value];
  if (row) chooseSource(row);
}

async function insertMentionToken(
  name: string,
  options: { useTrigger?: boolean; atEnd?: boolean; focus?: boolean } = {},
) {
  if (!name) return false;
  const area = editorTextarea();
  const focused = !!area && document.activeElement === area;
  let start: number;
  let end: number;
  if (options.useTrigger !== false && mentionTrigger.value) {
    start = mentionTrigger.value.start;
    end = mentionTrigger.value.end;
  } else {
    start = end =
      options.atEnd || !focused
        ? draft.value.length
        : (area?.selectionStart ?? draft.value.length);
  }
  const before = draft.value.slice(0, start);
  const after = draft.value.slice(end);
  const prefix = before && !/\s$/.test(before) ? " " : "";
  const suffix =
    after && !/^[\s,，。.!！?？;；:：、)）]/.test(after) ? " " : after ? "" : " ";
  const addition = `${prefix}@${name}${suffix}`;
  const next = before + addition + after;
  if (next.length > 2000) {
    emit("invalid", "输入内容不能超过 2000 字。");
    return false;
  }
  draft.value = next;
  finishMentionQuery();
  await nextTick();
  syncHighlightScroll();
  if (options.focus !== false && area) {
    const caret = before.length + addition.length;
    area.focus();
    area.setSelectionRange(caret, caret);
  }
  return true;
}

function stripMention(name: string, removeTrigger = false, focus = true) {
  const area = editorTextarea();
  const oldCaret = area?.selectionStart ?? draft.value.length;
  let text = draft.value;
  let caret = oldCaret;
  if (removeTrigger && mentionTrigger.value) {
    const { start, end } = mentionTrigger.value;
    text = text.slice(0, start) + text.slice(end);
    caret = start;
  }
  const ranges = mentionRangesIn(text, [name]);
  if (ranges.length) {
    let result = "";
    let cursor = 0;
    for (const range of ranges) {
      result += text.slice(cursor, range.start);
      cursor = text[range.end] === " " ? range.end + 1 : range.end;
    }
    result += text.slice(cursor);
    text = result;
    caret = Math.min(caret, text.length);
  }
  draft.value = text;
  finishMentionQuery();
  void nextTick(() => {
    syncHighlightScroll();
    if (!focus) return;
    const current = editorTextarea();
    if (!current) return;
    current.focus();
    const position = Math.max(0, Math.min(caret, text.length));
    current.setSelectionRange(position, position);
  });
}

function chooseSource(row: SourceFile) {
  if (props.busy || !rowSelectable(row)) return;
  const file = row.current_file!;
  if (isMentioned(row)) {
    emit("clear", file.id);
    stripMention(file.name, true, true);
    return;
  }
  if (!isAttached(row)) emit("attach", file);
  void insertMentionToken(file.name, { useTrigger: true, focus: true });
}
function toggleSource(row: SourceFile) {
  if (props.busy || !rowSelectable(row)) return;
  const file = row.current_file!;
  if (isAttached(row)) {
    emit("clear", file.id);
    stripMention(file.name, true, true);
    return;
  }
  emit("attach", file);
  void insertMentionToken(file.name, { useTrigger: true, focus: true });
}

function handleMentionKey(event: KeyboardEvent) {
  if (event.key === "Escape") {
    event.preventDefault();
    closeMention();
    return true;
  }
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    if (filteredSourceFiles.value.length)
      moveActive(event.key === "ArrowDown" ? 1 : -1);
    return true;
  }
  if (event.key === "Tab" && event.shiftKey) {
    closeMention();
    return false;
  }
  if (event.key === "Tab" || (event.key === "Enter" && !event.shiftKey)) {
    event.preventDefault();
    if (!filteredSourceFiles.value.length) closeMention();
    else if (!event.repeat) chooseActive();
    return true;
  }
  if (event.key === "Enter" && event.shiftKey) {
    closeMention();
    return false;
  }
  if (
    ["ArrowLeft", "ArrowRight", "Home", "End", "PageUp", "PageDown"].includes(
      event.key,
    )
  ) {
    closeMention();
  }
  return false;
}

function send() {
  if (!canSend.value) return;
  closeMention();
  emit("send");
}
function primaryAction() {
  if (showStop.value) {
    if (!stopDisabled.value) {
      closeMention();
      emit("stop");
    }
    return;
  }
  send();
}
function keydown(event: Event) {
  if (!(event instanceof KeyboardEvent)) return;
  // IME Enter confirms a candidate; keyCode 229 covers browsers that end
  // composition before dispatching the confirmation keydown.
  if (composing.value || event.isComposing || event.keyCode === 229) return;
  if (mentionOpen.value && handleMentionKey(event)) return;
  if (event.key !== "Enter" || event.shiftKey) return;
  event.preventDefault();
  if (!event.repeat) send();
}

function removeFile(file: FileInfo) {
  if (props.busy) return;
  closeMention();
  stripMention(file.name, false, false);
  emit("clear", file.id);
}
function projectFilesAction() {
  closeMention();
  emit("project-files");
}
function editorClick(event: MouseEvent) {
  if (mentionOpen.value && event.target instanceof HTMLTextAreaElement)
    closeMention();
}
function onDocumentPointer(event: PointerEvent) {
  if (!mentionOpen.value) return;
  const target = event.target as Node | null;
  if (target && composerEl.value?.contains(target)) return;
  closeMention();
}

function addMention(file: FileInfo) {
  if (!file.name || hasMentionToken(draft.value, file.name)) return;
  void insertMentionToken(file.name, {
    useTrigger: false,
    atEnd: true,
    focus: false,
  });
}
function removeMentionById(id: string) {
  const file = attachedFiles.value.find((item) => item.id === id);
  if (file) void stripMention(file.name, false, false);
}
function removeMentionByName(name: string) {
  if (name) void stripMention(name, false, false);
}

defineExpose({
  focus: (options: { atEnd?: boolean } = {}) => {
    inputRef.value?.focus();
    if (!options.atEnd) return;
    const area = editorTextarea();
    if (!area) return;
    const end = area.value.length;
    area.setSelectionRange(end, end);
  },
  addMention,
  removeMentionById,
  removeMentionByName,
});

const picker = ref<HTMLInputElement>();
const dragDepth = ref(0);
function select(files: FileList | null) {
  if (props.busy || !files?.length) return;
  const selected = Array.from(files);
  const pictures = selected.filter((file) => file.type.startsWith("image/"));
  const documents = selected.filter((file) => !file.type.startsWith("image/"));
  if (pictures.length) {
    if (pictures.length > 4) return emit("invalid", "每轮最多 4 张图片。");
    if (pictures.some((file) => file.size > 5 * 1024 * 1024))
      return emit("invalid", "图片不能超过 5 MiB。");
    emit("upload-images", pictures);
  }
  if (!documents.length) return;
  if (documents.length > 20) return emit("invalid", "每次最多选择 20 份文件。");
  for (const file of documents) {
    if (!file.name.toLowerCase().endsWith(".docx"))
      return emit("invalid", "目前仅支持 .docx 文件与图片。");
    if (file.size === 0) return emit("invalid", "不能上传空文件。");
    if (file.size > 10 * 1024 * 1024)
      return emit("invalid", "文件不能超过 10 MiB。");
  }
  emit("upload", documents);
}
function pasted(event: ClipboardEvent) {
  if (props.busy) return;
  const items = Array.from(event.clipboardData?.items ?? []);
  const files = items
    .filter((item) => item.kind === "file" && item.type.startsWith("image/"))
    .map((item) => item.getAsFile())
    .filter((file): file is File => !!file);
  if (!files.length) return;
  event.preventDefault();
  if (files.length > 4) return emit("invalid", "每轮最多 4 张图片。");
  if (files.some((file) => file.size > 5 * 1024 * 1024))
    return emit("invalid", "图片不能超过 5 MiB。");
  emit("upload-images", files);
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

let boundTextarea: HTMLTextAreaElement | null = null;
function bindTextareaEvents() {
  const area = editorTextarea();
  if (!area || area === boundTextarea) return;
  boundTextarea?.removeEventListener("scroll", syncHighlightScroll);
  boundTextarea?.removeEventListener("input", handleNativeInput);
  boundTextarea = area;
  area.addEventListener("scroll", syncHighlightScroll, { passive: true });
  area.addEventListener("input", handleNativeInput);
}
watch(draft, () => {
  void nextTick(syncHighlightScroll);
  if (!composing.value) composingText.value = null;
});
watch(
  () => props.projectId,
  () => closeMention(),
);
watch(
  () => props.busy,
  (busy) => {
    if (busy) closeMention();
  },
);
watch(
  () => props.sourceFiles,
  () => {
    if (!mentionOpen.value) return;
    activeIndex.value = firstSelectableIndex(filteredSourceFiles.value);
  },
);
onMounted(() => {
  bindTextareaEvents();
  void nextTick(bindTextareaEvents);
  window.addEventListener("resize", syncHighlightScroll);
  document.addEventListener("pointerdown", onDocumentPointer, true);
});
onBeforeUnmount(() => {
  boundTextarea?.removeEventListener("scroll", syncHighlightScroll);
  boundTextarea?.removeEventListener("input", handleNativeInput);
  window.removeEventListener("resize", syncHighlightScroll);
  document.removeEventListener("pointerdown", onDocumentPointer, true);
});
</script>