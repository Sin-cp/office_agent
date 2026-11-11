<template>
  <template v-for="(token, index) in tokens" :key="index">
    <span
      v-if="token.type === 'caret'"
      class="typing-caret"
      aria-hidden="true"
    ></span>
    <strong v-else-if="token.type === 'strong'">{{ token.text }}</strong>
    <code v-else-if="token.type === 'code'">{{ token.text }}</code>
    <template v-else>{{ token.text }}</template>
  </template>
</template>
<script setup lang="ts">
import type { InlineToken } from "../utils/markdown";
defineProps<{ tokens: InlineToken[] }>();
</script>
