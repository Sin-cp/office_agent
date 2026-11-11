<template>
  <section
    class="assistant-message"
    :class="`is-${task.status}`"
    aria-label="助手消息"
    data-testid="assistant-message"
  >
    <TaskProcess :task="task" />
    <AssistantAnswer
      v-if="task.result"
      :task="task"
      :reveal="answerArrived"
      @view="emit('view', $event)"
      @settled="answerSettled = true"
      @overwrite="emit('overwrite')"
    />
    <TurnActions
      v-if="showAnswerActions"
      class="answer-actions"
      variant="answer"
      :actions="answerActions"
      :copy-text="task.result?.text"
      @select="emit('action', $event)"
    />
  </section>
</template>
<script setup lang="ts">
import { computed, ref } from "vue";
import TaskProcess from "./TaskProcess.vue";
import AssistantAnswer from "./AssistantAnswer.vue";
import TurnActions from "./TurnActions.vue";
import type { FileInfo, TaskState } from "../api";

const props = defineProps<{ task: TaskState }>();
// A turn that is already finished when mounted (history load, page reload) is
// shown at once; a turn that finishes while the user watches reveals its answer
// progressively instead of dropping the whole paragraph in one frame.
const answerArrived = ref(!props.task.result);
// Answer controls wait for the reveal to finish so they never jump while text lands.
const answerSettled = ref(!answerArrived.value);
const emit = defineEmits<{
  view: [file: FileInfo];
  action: [key: "regenerate" | "delete"];
  overwrite: [];
}>();

const showAnswerActions = computed(
  () => props.task.status === "completed" && !!props.task.result && answerSettled.value,
);
const answerActions = computed<("copy" | "regenerate" | "delete")[]>(() =>
  props.task.result?.text ? ["copy", "regenerate", "delete"] : ["regenerate", "delete"],
);
</script>
