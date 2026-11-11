<template>
  <aside class="sidebar" aria-label="项目面板">
    <div class="sidebar-head">
      <a
        class="brand"
        href="/"
        aria-label="OfficeAgent 首页"
        @click.prevent="!switching && emit('new-session')"
      >
        <BrandMark class="brand-mark" />
        <span class="brand-name">Office<span>Agent</span></span>
        <el-tag class="brand-version" size="small" effect="plain" round
          >v1.0.0</el-tag
        >
      </a>
      <el-tooltip content="收起项目面板" placement="bottom" :show-after="400">
        <el-button
          class="chrome-button panel-toggle"
          text
          circle
          aria-label="收起项目面板"
          data-sidebar-toggle
          @click="emit('collapse')"
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="1.7"
            aria-hidden="true"
          >
            <rect x="3.4" y="4.4" width="17.2" height="15.2" rx="3.2" />
            <path d="M9.2 4.6v14.8" />
          </svg>
        </el-button>
      </el-tooltip>
    </div>

    <div class="sidebar-new">
      <el-button
        class="new-session-button"
        :disabled="busy"
        :aria-disabled="busy || switching"
        aria-label="新会话"
        @click="!switching && emit('new-session')"
      >
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="1.8"
          aria-hidden="true"
        >
          <path d="M12 5v14M5 12h14" stroke-linecap="round" />
        </svg>
        <span>新会话</span>
      </el-button>
    </div>

    <div class="projects-head">
      <span class="projects-title">项目</span>
      <span class="projects-count">{{ total }}</span>
      <el-tooltip content="刷新项目" placement="bottom" :show-after="400">
        <el-button
          class="project-refresh"
          text
          circle
          aria-label="刷新项目"
          :disabled="loading"
          @click="emit('retry')"
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="1.7"
            aria-hidden="true"
          >
            <path d="M20 11a8 8 0 1 0-2.2 5.6" stroke-linecap="round" />
            <path d="M20 4.5V11h-6.4" stroke-linecap="round" />
          </svg>
        </el-button>
      </el-tooltip>
    </div>

    <el-scrollbar class="projects-scroll">
      <div class="projects-body" :aria-busy="loading">
        <el-skeleton
          v-if="loading && !projects.length"
          class="project-skeleton"
          :rows="4"
          animated
        />
        <el-empty
          v-if="!projects.length && !loading && !error"
          :image-size="38"
          description="还没有项目"
        >
          <p class="empty-hint">
            点击「新会话」开始聊天，<br />也可以上传 DOCX 文档。
          </p>
        </el-empty>

        <ul v-if="projects.length" class="project-list">
          <li
            v-for="project in projects"
            :key="project.id"
            class="project-item"
            :class="{ active: project.id === activeProjectId }"
          >
            <button
              type="button"
              class="project-select"
              :disabled="busy"
              :aria-disabled="busy || switching"
              :aria-current="
                project.id === activeProjectId ? 'page' : undefined
              "
              :title="projectTitle(project)"
              @click="!switching && emit('select', project)"
            >
              <svg
                v-if="project.file_count"
                class="project-icon"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="1.6"
                aria-hidden="true"
              >
                <path
                  d="M6.5 3.8h7.1l4.4 4.4v11.9a1.6 1.6 0 0 1-1.6 1.6H6.5a1.6 1.6 0 0 1-1.6-1.6V5.4a1.6 1.6 0 0 1 1.6-1.6Z"
                  stroke-linejoin="round"
                />
                <path d="M13.3 3.9v4.6h4.6" stroke-linejoin="round" />
              </svg>
              <svg
                v-else
                class="project-icon"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="1.6"
                aria-hidden="true"
              >
                <path
                  d="M20 11.6a7.4 7.4 0 0 1-7.4 7.4H8.3L4 21.3V11.6a7.4 7.4 0 0 1 7.4-7.4h1.2a7.4 7.4 0 0 1 7.4 7.4Z"
                  stroke-linejoin="round"
                />
                <path d="M8.3 9.7h7.4M8.3 13.2h4.9" stroke-linecap="round" />
              </svg>
              <span class="project-name">{{ project.name }}</span>
              <span class="project-time">{{
                relativeTime(project.created_at)
              }}</span>
            </button>
            <el-dropdown
              class="project-actions"
              trigger="click"
              popper-class="project-menu"
              :disabled="busy || confirming || deleting"
              @command="(command: ProjectCommand) => onCommand(command, project)"
            >
              <el-button
                class="project-more"
                text
                circle
                :disabled="busy || confirming || deleting"
                :aria-label="`项目操作：${project.name}`"
                @click.stop
              >
                <svg
                  viewBox="0 0 24 24"
                  fill="currentColor"
                  aria-hidden="true"
                >
                  <circle cx="5" cy="12" r="1.6" />
                  <circle cx="12" cy="12" r="1.6" />
                  <circle cx="19" cy="12" r="1.6" />
                </svg>
              </el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item
                    v-if="project.file_count"
                    command="files"
                    >项目文件</el-dropdown-item
                  >
                  <el-dropdown-item command="rename">重命名</el-dropdown-item>
                  <el-dropdown-item command="delete" class="danger"
                    >删除项目</el-dropdown-item
                  >
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </li>
        </ul>
        <p v-if="loading && projects.length" class="list-status" role="status">
          正在加载项目…
        </p>

        <el-alert
          v-if="deleteError"
          class="feedback"
          type="error"
          :closable="false"
          :title="deleteError"
        />
        <p v-if="deleting" role="status" class="list-status">正在删除项目…</p>
        <el-alert
          v-if="error"
          class="feedback"
          type="error"
          :closable="false"
          :title="error"
        />
        <div v-if="error" class="list-retry">
          <el-button
            class="text-button"
            link
            :disabled="loading"
            @click="emit('retry')"
            >重新加载</el-button
          >
        </div>
        <el-button
          v-if="projects.length < total && !error"
          class="load-more"
          :disabled="loading"
          @click="emit('more')"
        >
          加载更多
        </el-button>
      </div>
    </el-scrollbar>

    <div class="sidebar-foot">
      <span class="sidebar-status-dot" aria-hidden="true"></span>
      <span>本地工作区</span>
    </div>
  </aside>
</template>
<script setup lang="ts">
import { ref } from "vue";
import {
  ElAlert,
  ElButton,
  ElDropdown,
  ElDropdownItem,
  ElDropdownMenu,
  ElEmpty,
  ElMessageBox,
  ElScrollbar,
  ElSkeleton,
  ElTag,
  ElTooltip,
} from "element-plus";
import BrandMark from "./BrandMark.vue";
import { deleteProject, renameProject, type ProjectInfo } from "../api";

type ProjectCommand = "files" | "rename" | "delete";

const props = defineProps<{
  projects: ProjectInfo[];
  total: number;
  activeProjectId?: string | null;
  busy?: boolean;
  switching?: boolean;
  loading: boolean;
  error: string;
}>();
const emit = defineEmits<{
  "new-session": [];
  collapse: [];
  select: [project: ProjectInfo];
  view: [project: ProjectInfo];
  renamed: [];
  deleted: [project: ProjectInfo];
  more: [];
  retry: [];
}>();

const confirming = ref(false);
const deleting = ref(false);
const deleteError = ref("");

function relativeTime(value: string | null) {
  if (!value) return "";
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) return "";
  const elapsed = Date.now() - timestamp;
  if (elapsed < 60_000) return "刚刚";
  if (elapsed < 3_600_000) return `${Math.floor(elapsed / 60_000)} 分钟`;
  if (elapsed < 86_400_000) return `${Math.floor(elapsed / 3_600_000)} 小时`;
  if (elapsed < 7 * 86_400_000) return `${Math.floor(elapsed / 86_400_000)} 天`;
  return new Date(value).toLocaleDateString("zh-CN", {
    month: "numeric",
    day: "numeric",
  });
}

function projectTitle(project: ProjectInfo) {
  const created = project.created_at
    ? new Date(project.created_at).toLocaleString("zh-CN")
    : "创建时间未知";
  const files = project.file_count
    ? `${project.file_count} 个文件`
    : "暂无文件";
  return `${project.name}\n${files} · 创建于 ${created}`;
}

function onCommand(command: ProjectCommand, project: ProjectInfo) {
  if (props.switching) return;
  if (command === "files") {
    emit("view", project);
    return;
  }
  if (command === "rename") {
    void rename(project);
    return;
  }
  void remove(project);
}

async function remove(project: ProjectInfo) {
  if (props.busy || props.switching || confirming.value || deleting.value) return;
  confirming.value = true;
  deleteError.value = "";
  try {
    await ElMessageBox.confirm(
      `确定删除项目“${project.name}”吗？项目文件、全部修改版和对话历史将一并删除，无法恢复！`,
      "删除项目",
      {
        confirmButtonText: "确认删除",
        cancelButtonText: "取消",
        type: "warning",
        confirmButtonClass: "delete-confirm-button",
        closeOnClickModal: false,
        lockScroll: false,
      },
    );
  } catch {
    return;
  } finally {
    confirming.value = false;
  }
  deleting.value = true;
  try {
    if (props.busy || props.switching) return;
    await deleteProject(project.id);
    emit("deleted", project);
  } catch (cause) {
    deleteError.value = (cause as Error).message;
  } finally {
    deleting.value = false;
  }
}

async function rename(project: ProjectInfo) {
  if (props.busy || props.switching || confirming.value || deleting.value) return;
  confirming.value = true;
  deleteError.value = "";
  try {
    const result = await ElMessageBox.prompt("输入项目名称", "重命名项目", {
      customClass: "project-rename-dialog",
      inputPlaceholder: "为项目起一个容易识别的名称",
      inputValue: project.name,
      inputValidator: (value) =>
        (!!value?.trim() && value.trim().length <= 100) ||
        "请输入 1–100 个字符",
      confirmButtonText: "保存",
      cancelButtonText: "取消",
      closeOnClickModal: false,
      lockScroll: false,
    });
    await renameProject(project.id, result.value.trim());
    emit("renamed");
  } catch (cause) {
    if (cause instanceof Error) deleteError.value = cause.message;
  } finally {
    confirming.value = false;
  }
}
</script>
