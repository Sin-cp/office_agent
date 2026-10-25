<script setup lang="ts">
import { onMounted, ref } from 'vue'

const status = ref<'loading' | 'ready' | 'error'>('loading')
const detail = ref('正在连接 FastAPI…')

async function checkHealth() {
  status.value = 'loading'
  detail.value = '正在连接 FastAPI…'
  try {
    const response = await fetch('/api/health', { signal: AbortSignal.timeout(5000) })
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    const data: unknown = await response.json()
    if (!data || typeof data !== 'object' || !('status' in data) || data.status !== 'ok') {
      throw new Error('Unexpected health response')
    }
    status.value = 'ready'
    detail.value = 'FastAPI 已连接，前后端运行正常'
  } catch {
    status.value = 'error'
    detail.value = '暂时无法连接后端，请确认 FastAPI 已在 8000 端口启动。'
  }
}

onMounted(checkHealth)
</script>

<template>
  <div class="layout">
    <header>
      <a class="brand" href="/">Office<span>Agent</span></a>
      <span class="version">v0.1.0 · 项目初始化</span>
    </header>
    <main>
      <div class="eyebrow">从第一步，构建你的文档助手</div>
      <h1>让文档修改，<br /><span>像对话一样简单。</span></h1>
      <p class="intro">这是 Office Agent 的起点。Vue 3 + TypeScript 负责交互，<br class="desktop" />FastAPI 提供后端服务，后续逐步接入文档编辑与 AI 工具调用。</p>
      <section class="workspace" aria-label="项目状态">
        <div class="workspace-header"><strong>开发工作台</strong><span>阶段 01</span></div>
        <div class="message"><div class="avatar">OA</div><div><strong>欢迎开始 Agent 开发之旅</strong><p>项目骨架已经就绪。先确认服务连接，下一步将实现 Word 文件上传与下载。</p></div></div>
        <div class="connection" :class="status" role="status" aria-live="polite">
          <span class="dot"></span><span>{{ detail }}</span>
          <button type="button" :disabled="status === 'loading'" @click="checkHealth">{{ status === 'loading' ? '检查中…' : '重新检查' }}</button>
        </div>
        <div class="next-step">下一步：上传 .docx → 保存文件 → 下载原文件</div>
      </section>
      <div class="stack"><span>Vue 3</span><span>TypeScript</span><span>FastAPI</span><span>Python</span></div>
    </main>
    <footer>一步一步，从 Web 应用到 AI Agent。</footer>
  </div>
</template>
