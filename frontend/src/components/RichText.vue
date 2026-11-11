<template>
  <div class="rich-text answer-text" data-testid="answer-body">
    <template v-for="(block, index) in blocks" :key="index">
      <h3 v-if="block.type === 'heading'" class="rich-heading">
        <InlineTokens :tokens="block.tokens" />
      </h3>
      <ul v-else-if="block.type === 'list' && !block.ordered">
        <li v-for="(item, itemIndex) in block.items" :key="itemIndex">
          <InlineTokens :tokens="item" />
        </li>
      </ul>
      <ol v-else-if="block.type === 'list'">
        <li v-for="(item, itemIndex) in block.items" :key="itemIndex">
          <InlineTokens :tokens="item" />
        </li>
      </ol>
      <p v-else>
        <InlineTokens :tokens="block.tokens" />
      </p>
    </template>
  </div>
</template>
<script setup lang="ts">
import { computed } from "vue";
import InlineTokens from "./InlineTokens.vue";
import { parseMarkdown, type InlineToken } from "../utils/markdown";

const props = withDefaults(defineProps<{ text: string; caret?: boolean }>(), {
  caret: false,
});
const blocks = computed(() => {
  const parsed = parseMarkdown(props.text);
  const last = parsed[parsed.length - 1];
  if (!props.caret || !last) return parsed;
  // Keep the blinking cursor glued to the last character: appending it as an
  // inline token renders it inside the final paragraph/list item.
  const marker: InlineToken = { type: "caret", text: "" };
  if (last.type === "list" && last.items.length) {
    last.items[last.items.length - 1]!.push(marker);
  } else {
    last.tokens.push(marker);
  }
  return parsed;
});
</script>
