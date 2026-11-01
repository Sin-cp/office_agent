<script setup lang="ts">
import { downloadUrl, type FileInfo, type TaskState } from '../api';
defineProps<{ task: TaskState; stopping: boolean }>();
defineEmits<{ stop: []; view: [file: FileInfo];  }>();
const labels = { queued: '等待处理', running: '正在处理', cancelling: '正在停止', completed: '修改完成', failed: '处理失败', cancelled: '已停止' };
function stepState(task: TaskState, index: number) {
  if (task.status === 'completed' || index < task.stage) return '已完成';
  if (index > task.stage || task.status === 'queued') return '等待';
  return labels[task.status];
}
</script>
<template>
  <section class="task-progress" aria-label="文档处理进度">
    <div class="task-heading"><h2>{{ labels[task.status] }}</h2><span>用时 {{ task.elapsed_seconds.toFixed(1) }} 秒</span></div>
    <p class="task-filename">{{ task.file.name }}</p>
    <ol aria-live="polite">
      <li v-for="(step, index) in task.steps" :key="step" :class="{current: index === task.stage}">
        <span>{{ step }}</span><small>{{ stepState(task, index) }}</small>
      </li>
    </ol>
    <button v-if="['queued', 'running', 'cancelling'].includes(task.status)" class="text-button" :disabled="stopping || task.status === 'cancelling'" @click="$emit('stop')">{{ stopping || task.status === 'cancelling' ? '正在停止…' : '停止处理' }}</button>
    <p v-if="task.error" class="feedback error" role="alert">失败阶段：{{ task.steps[task.stage] }}。{{ task.error }}</p>
    <p v-if="task.status === 'cancelled'">处理已停止，未生成修改版，原文件保持不变。</p>
    <div v-if="task.result" class="task-result">
      <h3>{{ task.result.file.name }}</h3>
      <details><summary>查看修改内容</summary><h4>原文</h4><p class="change-text">{{ task.result.before }}</p><h4>修改后</h4><p class="change-text">{{ task.result.after }}</p></details>
      <p v-if="task.result.file.available === false" class="deleted-result">此版本已删除或不可用，修改记录仍保留。</p>
      <div v-else class="task-actions">
        <a :href="downloadUrl(task.result.file.id)">下载文件 ↓</a>
        <button class="text-button" @click="$emit('view', task.result.file)">查看文件</button>
      </div>
    </div>
  </section>
</template>
