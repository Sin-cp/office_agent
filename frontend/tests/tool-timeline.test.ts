// @vitest-environment jsdom
import { mount } from '@vue/test-utils';
import { expect, it } from 'vitest';
import TaskProgress from '../src/components/TaskProgress.vue';
import type { TaskState, ToolEvent } from '../src/api';

const base: TaskState = {
  id: 'task',
  project_id: 'project',
  file: null,
  prompt: '查一下 2027 年放假安排',
  status: 'running',
  stage: 1,
  steps: ['读取会话', '理解请求', '处理内容', '保存结果'],
  elapsed_seconds: 6.4,
  error: null,
  result: null,
};

const events: ToolEvent[] = [
  {
    id: 'c1',
    tool: 'list_files',
    label: '查看项目文件',
    status: 'ok',
    started_at: 100,
    ended_at: 101.2,
    summary: '项目内 1 份文档',
  },
  {
    id: 'c2',
    tool: 'search_web',
    label: '检索网页「2027 年放假安排」',
    status: 'ok',
    started_at: 101.2,
    ended_at: 103.6,
    summary: '检索到 5 条结果',
  },
  {
    id: 'c3',
    tool: 'fetch_page',
    label: '读取 example.com',
    status: 'error',
    started_at: 103.6,
    ended_at: 106.6,
    summary: '抓取超时，已跳过',
  },
  {
    id: 'c4',
    tool: 'read_document',
    label: '读取《攻略.docx》',
    status: 'running',
    started_at: 106.6,
  },
];

it('renders one row per tool call while the turn runs', () => {
  const wrapper = mount(TaskProgress, {
    props: { task: { ...base, tools: events } },
  });
  const rows = wrapper.findAll('[data-testid="tool-row"]');
  expect(rows).toHaveLength(4);
  expect(rows[0]!.text()).toContain('查看项目文件');
  expect(rows[0]!.text()).toContain('1.2s');
  expect(rows[2]!.text()).toContain('抓取超时');
  expect(rows[3]!.text()).toContain('…');
  // No progress meter once tool events exist.
  expect(wrapper.find('.process-meter').exists()).toBe(false);
  wrapper.unmount();
});

it('collapses finished tool calls into one summary line', async () => {
  const finished = events.slice(0, 3).map((event) => ({
    ...event,
    status: 'ok' as const,
    ended_at: (event.ended_at ?? 0) || 108,
  }));
  const wrapper = mount(TaskProgress, {
    props: {
      task: {
        ...base,
        status: 'completed',
        stage: 3,
        result: { kind: 'answer', text: '已查到安排。' },
        tools: finished,
      },
    },
  });
  expect(wrapper.find('[data-testid="tool-row"]').exists()).toBe(false);
  expect(wrapper.get('[data-testid="tool-summary"]').text()).toContain('已完成 3 步');
  await wrapper.get('[data-testid="process-toggle"]').trigger('click');
  expect(wrapper.findAll('[data-testid="tool-row"]')).toHaveLength(3);
  wrapper.unmount();
});

it('counts skipped steps in the summary', () => {
  const wrapper = mount(TaskProgress, {
    props: {
      task: {
        ...base,
        status: 'completed',
        stage: 3,
        result: { kind: 'answer', text: '部分内容未能核实。' },
        tools: events.filter((event) => event.status !== 'running'),
      },
    },
  });
  expect(wrapper.get('[data-testid="tool-summary"]').text()).toContain('1 步跳过');
  wrapper.unmount();
});

it('keeps the classic step detail when a task has no tool events', () => {
  const wrapper = mount(TaskProgress, {
    props: { task: { ...base, tools: [] } },
  });
  expect(wrapper.find('[data-testid="tool-timeline"]').exists()).toBe(false);
  expect(wrapper.find('.process-meter').exists()).toBe(true);
  wrapper.unmount();
});
