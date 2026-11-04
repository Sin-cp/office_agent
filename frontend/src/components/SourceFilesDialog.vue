<script setup lang="ts">
import { onMounted, ref } from 'vue';
import DocumentEditor from './DocumentEditor.vue';
import { deleteSourceFile, projectFiles, downloadUrl, formatSize, type SourceFile, type FileInfo } from '../api';
const props = defineProps<{ projectId: string; selectable?: boolean; selectedIds?: string[]; busy?: boolean }>();
const emit = defineEmits<{ close: []; select: [file: FileInfo]; deselect: [id: string]; deleted: [file: SourceFile] }>();
const dialog = ref<HTMLDialogElement>();
const items = ref<SourceFile[]>([]), loading = ref(false), error = ref('');
const confirming = ref<SourceFile | null>(null), deleting = ref(false), deleteError = ref('');
async function remove() {
  const file = confirming.value;
  if (!file || props.busy || deleting.value) return;
  deleting.value = true; deleteError.value = '';
  try {
    await deleteSourceFile(props.projectId, file.id);
    items.value = items.value.filter(item => item.id !== file.id);
    emit('deleted', file);
    confirming.value = null;
  } catch (cause) { deleteError.value = (cause as Error).message; }
  finally { deleting.value = false; }
}
const preview = ref<FileInfo | null>(null);
async function load() {
  loading.value = true; error.value = '';
  try { items.value = (await projectFiles(props.projectId)).items; }
  catch (cause) { error.value = (cause as Error).message; }
  finally { loading.value = false; }
}
function cancel(event: Event) {
  if (deleting.value) { event.preventDefault(); return; }
  if (confirming.value) { event.preventDefault(); confirming.value = null; return; }
  if (preview.value) { event.preventDefault(); preview.value = null; }
  else emit('close');
}
onMounted(() => { if (dialog.value?.showModal) dialog.value.showModal(); else dialog.value?.setAttribute('open', ''); void load(); });
</script>
<template>
  <Teleport to="body">
    <dialog ref="dialog" class="source-dialog" aria-label="项目项目文件" @cancel="cancel" @close="emit('close')">
      <template v-if="!preview">
        <div class="source-heading"><h2>项目文件（{{ items.length }}）</h2><button class="text-button" :disabled="deleting" @click="emit('close')">关闭</button></div>
        <p v-if="loading" role="status">正在加载项目文件…</p>
        <p v-if="error" role="alert">{{ error }} <button @click="load">重试</button></p>
        <p v-if="!loading && !error && !items.length">此项目还没有项目文件。</p>
        <table v-if="items.length" class="source-table">
          <thead><tr><th v-if="selectable">本轮附件</th><th>文件名</th><th>来源</th><th>大小</th><th>创建时间</th><th>操作</th></tr></thead>
          <tbody><tr v-for="file in items" :key="file.id">
            <td v-if="selectable"><input type="checkbox" :aria-label="`选择 ${file.name}`" :disabled="busy || deleting || !file.current_file || file.current_file.available === false" :checked="!!file.current_file && selectedIds?.includes(file.current_file.id)" @change="file.current_file && ($event.target as HTMLInputElement).checked ? emit('select', file.current_file!) : file.current_file && emit('deselect', file.current_file.id)" /></td>
            <td>{{ file.name }}</td><td><span class="file-origin" :class="{generated: file.origin === 'generated'}">{{ file.origin === 'generated' ? '生成' : '上传' }}</span></td><td>{{ file.size == null ? '未知' : formatSize(file.size) }}</td><td>{{ file.created_at ? new Date(file.created_at).toLocaleString('zh-CN') : '未知' }}</td>
            <td><div class="source-actions"><template v-if="file.available"><a class="source-action source-download" :href="downloadUrl(file.id)">下载</a><button class="source-action source-preview" :disabled="deleting" @click="preview = file">预览</button></template><span v-else>项目文件不可用</span><button class="source-action source-delete" :aria-label="`删除 ${file.name}`" :disabled="busy || deleting" @click="confirming = file; deleteError = ''">删除</button></div></td>
          </tr></tbody>
        </table>
        <div v-if="confirming" class="source-delete-confirm" role="alert">
          <p>确定删除“{{ confirming.name }}”吗？项目文件将永久删除，聊天记录和已有修改结果保留。</p>
          <div class="source-actions"><button class="source-action source-preview" :disabled="deleting" @click="confirming = null">取消</button><button class="source-action source-delete" :disabled="busy || deleting" @click="remove">{{ deleting ? '删除中…' : '确认删除' }}</button></div>
          <p v-if="deleteError">{{ deleteError }}</p>
        </div>
        <p v-if="selectable">选中的附件用于本轮对话；已有修改结果时，处理其最新结果。预览和下载始终对应项目文件。</p>
      </template>
      <DocumentEditor v-else :key="preview.id" :file="preview" @close="preview = null" />
    </dialog>
  </Teleport>
</template>
