// @vitest-environment jsdom
import { mount, flushPromises } from '@vue/test-utils';
import { expect, it, vi } from 'vitest';
import TaskProgress from '../src/components/TaskProgress.vue';
import type { TaskState } from '../src/api';

const file = {
  id: 'new',
  name: '结婚男方准备清单.docx',
  size: 1024,
  created_at: '2026-11-05T12:00:00Z',
  available: true,
};
const steps = ['读取文档与会话', '理解请求', '校验结果', '保存结果'];
const base: TaskState = {
  id: 'task',
  project_id: 'project',
  file: null,
  prompt: '生成一份清单',
  status: 'running',
  stage: 1,
  steps,
  elapsed_seconds: 1.2,
  error: null,
  result: null,
};
const completedAnswer: TaskState = {
  ...base,
  status: 'completed',
  stage: 3,
  elapsed_seconds: 1.2,
  result: {
    kind: 'answer',
    text: '今天北京以晴为主。\n\n**天气概况**\n- 白天晴\n- 夜间冷\n\n请带外套。',
  },
};

it('shows a compact live process while running', async () => {
  const wrapper = mount(TaskProgress, {
    props: { task: base },
  });
  expect(wrapper.get('.assistant-status').text()).toContain('正在处理');
  expect(wrapper.get('.assistant-elapsed').text()).toContain('用时 1.2 秒');
  expect(wrapper.findAll('.process-segment')).toHaveLength(4);
  expect(wrapper.get('.process-segment.active').exists()).toBe(true);
  expect(wrapper.find('[data-testid="process-detail"]').exists()).toBe(false);

  expect(wrapper.find('[data-testid="stop-task"]').exists()).toBe(false);

  await wrapper.get('[data-testid="process-toggle"]').trigger('click');
  expect(wrapper.get('[data-testid="process-toggle"]').attributes('aria-expanded')).toBe('true');
  expect(wrapper.findAll('[data-testid="process-step"]')).toHaveLength(4);
  expect(wrapper.get('[data-testid="process-step"]').attributes('data-state')).toBe('done');
  expect(wrapper.findAll('[data-testid="process-step"]')[1]!.attributes('data-state')).toBe('active');
  wrapper.unmount();
});

it('collapses a running process when the task completes', async () => {
  const wrapper = mount(TaskProgress, {
    props: { task: base },
  });
  await wrapper.get('[data-testid="process-toggle"]').trigger('click');
  expect(wrapper.find('[data-testid="process-detail"]').exists()).toBe(true);
  await wrapper.setProps({ task: completedAnswer });
  await flushPromises();
  expect(wrapper.find('[data-testid="process-detail"]').exists()).toBe(false);
  expect(wrapper.get('[data-testid="process-toggle"]').attributes('aria-expanded')).toBe('false');
  expect(wrapper.get('.assistant-status').text()).toContain('已回答');
  wrapper.unmount();
});

it('reveals a freshly completed answer progressively, not all at once', async () => {
  vi.useFakeTimers();
  try {
    const wrapper = mount(TaskProgress, { props: { task: base } });
    await wrapper.setProps({ task: completedAnswer });
    await flushPromises();
    // Nothing yet: the reveal waits for the collapsing process block.
    expect(wrapper.find('[data-testid="answer-body"]').exists()).toBe(false);
    expect(wrapper.find('.result-files').exists()).toBe(false);

    await vi.advanceTimersByTimeAsync(420);
    const partial = wrapper.get('[data-testid="answer-body"]').text();
    expect(partial.length).toBeGreaterThan(0);
    expect(partial.length).toBeLessThan(completedAnswer.result!.text!.length);
    expect(wrapper.find('.typing-caret').exists()).toBe(true);
    // Answer controls stay hidden while the text is still landing.
    expect(wrapper.find('.answer-actions').exists()).toBe(false);

    await vi.advanceTimersByTimeAsync(2400);
    await flushPromises();
    expect(wrapper.get('[data-testid="answer-body"]').text()).toContain('请带外套。');
    expect(wrapper.findAll('.rich-text li')).toHaveLength(2);
    expect(wrapper.find('.typing-caret').exists()).toBe(false);
    expect(wrapper.findAll('.answer-actions [data-testid]')).toHaveLength(3);
    wrapper.unmount();
  } finally {
    vi.useRealTimers();
  }
});

it('renders answer markdown safely without injecting HTML', async () => {
  const wrapper = mount(TaskProgress, {
    props: { task: completedAnswer },
  });
  expect(wrapper.get('[data-testid="answer-body"]').text()).toContain('今天北京以晴为主');
  expect(wrapper.findAll('.rich-text .rich-heading')).toHaveLength(1);
  expect(wrapper.findAll('.rich-text li')).toHaveLength(2);
  expect(wrapper.find('strong').exists()).toBe(false);
  wrapper.unmount();

  const unsafe: TaskState = {
    ...completedAnswer,
    result: {
      kind: 'answer',
      text: '<img src=x onerror="alert(1)">\n**安全标题**',
    },
  };
  const escaped = mount(TaskProgress, {
    props: { task: unsafe },
  });
  expect(escaped.find('img').exists()).toBe(false);
  expect(escaped.text()).toContain('<img src=x onerror="alert(1)">');
  expect(escaped.findAll('.rich-heading')).toHaveLength(1);
  escaped.unmount();
});

it('renders a generated file card with download and view actions', async () => {
  const generated: TaskState = {
    ...base,
    status: 'completed',
    stage: 3,
    elapsed_seconds: 10,
    result: {
      kind: 'create',
      text: '文档已生成并保存到当前项目。',
      file,
      outputs: [{ file, changes: [] }],
    },
  };
  const wrapper = mount(TaskProgress, {
    props: { task: generated },
  });
  expect(wrapper.text()).toContain('文档已生成');
  expect(wrapper.text()).toContain('文档已生成并保存到当前项目。');
  expect(wrapper.get('[data-testid="result-file"]').text()).toContain(file.name);
  expect(
    wrapper.get('[data-testid="download-file"]').attributes('href'),
  ).toBe(`/api/files/${file.id}/download`);
  expect(wrapper.findAll('.task-actions a')).toHaveLength(1);

  await wrapper.get('[data-testid="view-file"]').trigger('click');
  expect(wrapper.emitted('view')).toEqual([[file]]);
  wrapper.unmount();
});

it('groups multiple edited files and exposes their changes', async () => {
  const other = { ...file, id: 'other', name: '第二份.docx' };
  const edited: TaskState = {
    ...base,
    status: 'completed',
    stage: 3,
    result: {
      kind: 'edit',
      text: '已修改两处',
      outputs: [
        {
          file,
          changes: [
            {
              op: 'replace',
              block_id: 'a',
              location: '正文第1段',
              before: '旧内容',
              after: '新内容',
            },
          ],
        },
        {
          file: other,
          changes: [
            {
              op: 'insert_paragraph',
              block_id: 'b',
              location: '正文第2段',
              before: '',
              after: '新增内容',
            },
          ],
        },
      ],
    },
  };
  const wrapper = mount(TaskProgress, {
    props: { task: edited },
  });
  expect(wrapper.text()).toContain('修改完成');
  expect(wrapper.findAll('[data-testid="result-file"]')).toHaveLength(2);
  expect(wrapper.findAll('.task-actions a')).toHaveLength(2);
  expect(wrapper.findAll('[data-testid="change-card"]')).toHaveLength(2);
  expect(
    wrapper.get('[data-testid="change-toggle"]').attributes('aria-expanded'),
  ).toBe('false');
  await wrapper.get('[data-testid="change-toggle"]').trigger('click');
  expect(
    wrapper.get('[data-testid="change-toggle"]').attributes('aria-expanded'),
  ).toBe('true');
  expect(wrapper.text()).toContain('正文第1段');
  expect(wrapper.text()).toContain('旧内容');
  expect(wrapper.text()).toContain('新增内容');
  wrapper.unmount();
});

it('shows failure detail and no result actions', async () => {
  const failed: TaskState = {
    ...base,
    status: 'failed',
    error: '模型响应超时',
  };
  const wrapper = mount(TaskProgress, {
    props: { task: failed },
  });
  expect(wrapper.text()).toContain('处理失败');
  expect(wrapper.text()).toContain('失败阶段：理解请求');
  expect(wrapper.text()).toContain('模型响应超时');
  expect(wrapper.find('.process-error').attributes('role')).toBe('alert');
  expect(wrapper.find('[data-testid="process-detail"]').exists()).toBe(true);
  expect(wrapper.find('.task-actions').exists()).toBe(false);
  wrapper.unmount();
});

it('shows cancelled state without a live status or result', () => {
  const cancelled: TaskState = {
    ...base,
    status: 'cancelled',
    error: null,
  };
  const wrapper = mount(TaskProgress, {
    props: { task: cancelled },
  });
  expect(wrapper.text()).toContain('已停止');
  expect(wrapper.text()).toContain('未发布文件结果');
  expect(wrapper.find('[role="status"]').exists()).toBe(false);
  expect(wrapper.find('[data-testid="result-file"]').exists()).toBe(false);
  wrapper.unmount();
});

it('keeps source links safe and collapses them by default', async () => {
  const sourced: TaskState = {
    ...completedAnswer,
    result: {
      ...completedAnswer.result,
      web_sources: [
        {
          title: '天气资料',
          url: 'https://example.com/weather',
          retrieved_at: '2026-11-05T10:00:00+08:00',
          kind: 'page',
        },
        {
          title: '不安全链接',
          url: 'javascript:alert(1)',
          retrieved_at: '2026-11-05T10:00:00+08:00',
          kind: 'search',
        },
      ],
    },
  };
  const wrapper = mount(TaskProgress, {
    props: { task: sourced },
  });
  expect(
    wrapper.get('[data-testid="sources-toggle"]').attributes('aria-expanded'),
  ).toBe('false');
  await wrapper.get('[data-testid="sources-toggle"]').trigger('click');
  expect(
    wrapper.get('[data-testid="sources-toggle"]').attributes('aria-expanded'),
  ).toBe('true');
  const links = wrapper.findAll('.web-sources a');
  expect(links).toHaveLength(1);
  expect(links[0]!.attributes('href')).toBe('https://example.com/weather');
  expect(links[0]!.attributes('rel')).toBe('noopener noreferrer');
  wrapper.unmount();
});

it('shows live activity through one status region only while active', () => {
  const running: TaskState = {
    ...base,
    web_activity: '正在搜索',
    activity: '正在补充遗漏内容（1/2）',
  };
  const wrapper = mount(TaskProgress, {
    props: { task: running },
  });
  const status = wrapper.get('[role="status"]').text();
  expect(status).toContain('正在搜索');
  expect(status).toContain('正在补充遗漏内容（1/2）');
  wrapper.unmount();

  const finished = mount(TaskProgress, {
    props: { task: { ...completedAnswer, activity: '正在检查修改完整性' } },
  });
  expect(finished.find('[role="status"]').exists()).toBe(false);
  finished.unmount();
});
