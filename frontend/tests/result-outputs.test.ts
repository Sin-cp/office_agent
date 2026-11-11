// @vitest-environment jsdom
import { mount } from '@vue/test-utils';
import { expect, it } from 'vitest';
import TaskProgress from '../src/components/TaskProgress.vue';
import type { TaskState } from '../src/api';

const base: TaskState = {
  id: 'task',
  project_id: 'p',
  file: null,
  prompt: '先查再建',
  status: 'completed',
  stage: 3,
  steps: ['读取会话', '理解请求', '处理内容', '保存结果'],
  elapsed_seconds: 3,
  error: null,
  result: null,
};

it('renders each output with its own kind in one turn', () => {
  const wrapper = mount(TaskProgress, {
    props: {
      task: {
        ...base,
        result: {
          kind: 'edit',
          text: '已生成一份文档并修改了另一份。',
          outputs: [
            {
              file: { id: 'a', name: '新建.docx', size: 1, created_at: '2026-11-09' },
              changes: [],
              kind: 'create',
            },
            {
              file: { id: 'b', name: '修改版.docx', size: 1, created_at: '2026-11-09' },
              changes: [],
              kind: 'edit',
            },
          ],
        },
      },
    },
  });
  const cards = wrapper.findAll('[data-testid="result-file"]');
  expect(cards).toHaveLength(2);
  expect(cards[0]!.text()).toContain('已生成');
  expect(cards[1]!.text()).not.toContain('已生成');
  wrapper.unmount();
});

it('marks only the renamed output when a turn has several results', () => {
  const wrapper = mount(TaskProgress, {
    props: {
      task: {
        ...base,
        result: {
          kind: 'edit',
          text: '一份改名，一份改正文。',
          outputs: [
            {
              file: { id: 'a', name: '新名字.docx', size: 1, created_at: '2026-11-09' },
              changes: [],
              kind: 'edit',
              renamed: true,
            },
            {
              file: { id: 'b', name: '修改版.docx', size: 1, created_at: '2026-11-09' },
              changes: [],
              kind: 'edit',
              renamed: false,
            },
          ],
        },
      },
    },
  });
  const cards = wrapper.findAll('[data-testid="result-file"]');
  expect(cards).toHaveLength(2);
  expect(cards[0]!.text()).toContain('已重命名');
  expect(cards[1]!.text()).toContain('已修改');
  expect(cards[1]!.text()).not.toContain('已重命名');
  wrapper.unmount();
});
