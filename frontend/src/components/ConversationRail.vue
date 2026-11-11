<template>
  <nav v-if="turns.length > 1" ref="railEl" class="turn-rail" aria-label="对话锚点">
    <div class="rail-list">
      <button
        v-for="(turn, index) in turns"
        :key="turn.id"
        type="button"
        class="turn-rail-item"
        :class="{ 'is-active': activeId === turn.id }"
        :data-rail-id="turn.id"
        :aria-current="activeId === turn.id ? 'true' : undefined"
        :aria-label="`第 ${index + 1} 轮：${turn.prompt}`"
        @mouseenter="hover(turn.id)"
        @mouseleave="hoverId = null"
        @focus="hover(turn.id)"
        @blur="hoverId = null"
        @click="jump(turn.id)"
      >
        <span class="rail-dash" aria-hidden="true"></span>
      </button>
    </div>
    <span
      v-if="hoveredTurn"
      class="rail-tip"
      :style="{ top: `${tipTop}px` }"
      aria-hidden="true"
      >{{ hoveredTurn.prompt }}</span
    >
  </nav>
</template>
<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";

const props = defineProps<{
  turns: { id: string; prompt: string }[];
  scrollArea?: HTMLElement | null;
}>();

const railEl = ref<HTMLElement>();
const activeId = ref<string | null>(null);
const hoverId = ref<string | null>(null);
const tipTop = ref(0);
const hoveredTurn = computed(
  () => props.turns.find((turn) => turn.id === hoverId.value) ?? null,
);
let frame = 0;

/** The prompt label lives outside the scrolling dash list so nothing clips it. */
async function hover(id: string) {
  hoverId.value = id;
  await nextTick();
  const rail = railEl.value;
  const button = rail?.querySelector<HTMLElement>(`[data-rail-id="${id}"]`);
  if (!rail || !button) return;
  const buttonBox = button.getBoundingClientRect();
  const railBox = rail.getBoundingClientRect();
  tipTop.value = Math.round(buttonBox.top + buttonBox.height / 2 - railBox.top);
}

function elements() {
  const area = props.scrollArea;
  if (!area) return [] as HTMLElement[];
  return [...area.querySelectorAll<HTMLElement>("[data-task-id]")];
}

/** The active turn is the last one that starts above the reading line (25% down). */
function measure() {
  const area = props.scrollArea;
  const items = elements();
  if (!area || !items.length) {
    activeId.value = null;
    return;
  }
  const box = area.getBoundingClientRect();
  const line = box.top + Math.min(160, Math.max(60, box.height * 0.25));
  let current = items[0]!.dataset.taskId ?? null;
  for (const item of items) {
    if (item.getBoundingClientRect().top <= line) current = item.dataset.taskId ?? current;
  }
  activeId.value = current;
}

function schedule() {
  if (frame) return;
  const run = () => {
    frame = 0;
    measure();
  };
  frame =
    typeof requestAnimationFrame === "function"
      ? requestAnimationFrame(run)
      : (setTimeout(run, 16) as unknown as number);
}

function cancel() {
  if (!frame) return;
  if (typeof cancelAnimationFrame === "function") cancelAnimationFrame(frame);
  clearTimeout(frame);
  frame = 0;
}

function reducedMotion() {
  return (
    typeof window !== "undefined" &&
    window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches === true
  );
}

function jump(id: string) {
  const area = props.scrollArea;
  const target = elements().find((item) => item.dataset.taskId === id);
  if (!area || !target) return;
  const top =
    target.getBoundingClientRect().top -
    area.getBoundingClientRect().top +
    area.scrollTop -
    12;
  activeId.value = id;
  area.scrollTo({ top: Math.max(0, top), behavior: reducedMotion() ? "auto" : "smooth" });
}

watch(
  () => props.scrollArea,
  (area, previous) => {
    previous?.removeEventListener("scroll", schedule);
    area?.addEventListener("scroll", schedule, { passive: true });
    // Measure on the next frame: the host may still be restoring scrollTop.
    schedule();
  },
  { immediate: true },
);
watch(() => props.turns, () => schedule(), { deep: true });

if (typeof window !== "undefined") window.addEventListener("resize", schedule);

onBeforeUnmount(() => {
  props.scrollArea?.removeEventListener("scroll", schedule);
  if (typeof window !== "undefined") window.removeEventListener("resize", schedule);
  cancel();
});
</script>
