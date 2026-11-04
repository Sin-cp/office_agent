<script setup lang="ts">
import { downloadUrl, type FileInfo, type TaskState } from '../api';
defineProps<{ task: TaskState; stopping: boolean }>();
defineEmits<{ stop: []; view: [file: FileInfo];  }>();
const labels = { queued: '等待处理', running: '正在处理', cancelling: '正在停止', completed: '处理完成', failed: '处理失败', cancelled: '已停止' };
function stepState(task: TaskState, index: number) {
  if (task.status === 'completed' || index < task.stage) return '已完成';
  if (index > task.stage || task.status === 'queued') return '等待';
  return labels[task.status];
}
function safeSource(url: string) {
  try { const parsed = new URL(url); return ['http:', 'https:'].includes(parsed.protocol) && !parsed.username && !parsed.password; }
  catch { return false; }
}
</script>
<template>
  <section class="task-progress" aria-label="文档处理进度">
    <div v-if="task.status !== 'completed' || task.result?.file || task.result?.outputs?.length" class="task-heading"><h2>{{ task.status === 'completed' && (task.result?.file || task.result?.outputs?.length) ? (task.result?.kind === 'create' ? '文档已生成' : '修改完成') : labels[task.status] }}</h2><span>用时 {{ task.elapsed_seconds.toFixed(1) }} 秒</span></div>
    <p v-if="task.status !== 'completed' || task.result?.file || task.result?.outputs?.length" class="task-filename">{{ task.files?.map(f => f.name).join('、') || task.file?.name }}</p>
    <ol v-if="task.status !== 'completed'" aria-live="polite">
      <li v-for="(step, index) in task.steps" :key="step" :class="{current: index === task.stage}">
        <span>{{ step }}</span><small>{{ stepState(task, index) }}</small>
      </li>
    </ol>
    <button v-if="['queued', 'running', 'cancelling'].includes(task.status)" class="text-button" :disabled="stopping || task.status === 'cancelling'" @click="$emit('stop')">{{ stopping || task.status === 'cancelling' ? '正在停止…' : '停止处理' }}</button>
    <p v-if="task.activity && ['queued', 'running', 'cancelling'].includes(task.status)" class="web-activity" role="status">{{ task.activity }}…</p>
    <p v-if="task.web_activity && ['queued', 'running', 'cancelling'].includes(task.status)" class="web-activity" role="status">{{ task.web_activity }}…</p>
    <p v-if="task.error" class="feedback error" role="alert">失败阶段：{{ task.steps[task.stage] }}。{{ task.error }}</p>
    <p v-if="task.status === 'cancelled'">处理已停止，未发布文件结果，已有文件保持不变。</p>
    <div v-if="task.result" class="task-result">
      <p v-if="task.result.text" class="answer-text">{{ task.result.text }}</p>
      <p v-for="notice in task.result.web_notices || []" :key="notice.code" class="web-notice">{{ notice.message }}</p>
      <div v-if="task.result.web_sources?.length" class="web-sources" aria-label="查询来源">
        <span>查询来源</span>
        <ol>
          <li v-for="source in task.result.web_sources" :key="source.url">
            <a v-if="safeSource(source.url)" :href="source.url" target="_blank" rel="noopener noreferrer" referrerpolicy="no-referrer">{{ source.title }}</a>
            <span v-else>{{ source.title }}</span>
            <small>{{ source.kind === 'search' ? '搜索摘要' : '网页正文' }} · 查询于 {{ new Date(source.retrieved_at).toLocaleString('zh-CN') }}</small>
          </li>
        </ol>
      </div>
      <template v-for="output in (task.result.outputs || (task.result.file ? [{file: task.result.file, changes: task.result.changes}] : []))" :key="output.file.id">
        <h3>{{ output.file.name }}</h3>
        <details v-if="task.result.kind !== 'create'"><summary>查看修改内容</summary>
          <template v-if="output.changes?.length">
            <section v-for="(change, index) in output.changes" :key="index">
              <h4>{{ change.location }}</h4>
              <h5>原文</h5><p class="change-text">{{ change.before || '（新增）' }}</p>
              <h5>修改后</h5><p class="change-text">{{ change.after || '（已删除或清空）' }}</p>
            </section>
          </template>
          <template v-else><h4>原文</h4><p class="change-text">{{ task.result.before }}</p><h4>修改后</h4><p class="change-text">{{ task.result.after }}</p></template>
        </details>
        <p v-if="output.file.available === false" class="deleted-result">此文件已删除或不可用，对话记录仍保留。</p>
        <div v-else class="task-actions">
          <a :href="downloadUrl(output.file.id)">下载文件 ↓</a>
          <button class="text-button" @click="$emit('view', output.file)">查看文件</button>
        </div>
      </template>
    </div>
  </section>
</template>
