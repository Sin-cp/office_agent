// @vitest-environment jsdom
import { mount, flushPromises } from '@vue/test-utils';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { ElMessageBox } from 'element-plus';
import App from '../src/App.vue';
import AssistantAnswer from '../src/components/AssistantAnswer.vue';
import type { TaskState } from '../src/api';
import * as api from '../src/api';

vi.mock('../src/api', async (original) => ({
  ...(await original<typeof api>()),
  createProject: vi.fn(),
  projectFiles: vi.fn(),
  projectHistory: vi.fn(),
  listProjects: vi.fn(),
  uploadFile: vi.fn(),
  readDocument: vi.fn(),
  overwriteLocalTask: vi.fn(),
}));

const generated = {
  id: 'new',
  name: '测试_修改版1.docx',
  size: 100,
  created_at: '2026-11-06',
  available: true,
};

const localEdit = {
  path: '/Users/Sin/测试.docx',
  name: '测试.docx',
  source_file_id: 'src',
  new_file_id: 'new',
  source_sha256: 'abc',
};

function task(overrides: Partial<TaskState> = {}): TaskState {
  return {
    id: 'task',
    project_id: 'project',
    file: generated,
    files: [],
    prompt: '修改这个文档',
    status: 'completed',
    stage: 3,
    steps: [],
    elapsed_seconds: 1,
    error: null,
    result: {
      kind: 'edit',
      text: '已新增一段。原文件 /Users/Sin/测试.docx 未改动。',
      file: generated,
      changes: [],
      outputs: [{ file: generated, changes: [] }],
      local_edit: { ...localEdit },
    },
    ...overrides,
  } as TaskState;
}

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

describe('local file overwrite', () => {
  it('shows the untouched source file and emits overwrite on click', async () => {
    const wrapper = mount(AssistantAnswer, { props: { task: task() } });
    const strip = wrapper.get('[data-testid="local-file"]');
    expect(strip.text()).toContain('原文件未改动');
    expect(strip.text()).toContain('/Users/Sin/测试.docx');
    const button = wrapper.get('[data-testid="overwrite-local"]');
    expect(button.attributes('disabled')).toBeUndefined();
    await button.trigger('click');
    expect(wrapper.emitted('overwrite')).toHaveLength(1);
    wrapper.unmount();
  });

  it('shows the backup and hides the button once overwritten', () => {
    const result = {
      ...task().result!,
      local_edit: {
        ...localEdit,
        overwritten: {
          at: '2026-11-06T10:00:00Z',
          backup: '/Users/Sin/测试.docx.bak-20261106-180000',
        },
      },
    };
    const wrapper = mount(AssistantAnswer, {
      props: { task: task({ result }) },
    });
    const strip = wrapper.get('[data-testid="local-file"]');
    expect(strip.text()).toContain('已覆盖源文件');
    expect(strip.text()).toContain('.bak-');
    expect(wrapper.find('[data-testid="overwrite-local"]').exists()).toBe(false);
    wrapper.unmount();
  });

  it('disables the button before the task completes', () => {
    const wrapper = mount(AssistantAnswer, {
      props: { task: task({ status: 'running' }) },
    });
    expect(
      wrapper.get('[data-testid="overwrite-local"]').attributes('disabled'),
    ).toBeDefined();
    wrapper.unmount();
  });

  it('confirms and calls the overwrite endpoint from the conversation', async () => {
    const project = {
      id: 'project',
      name: '测试项目',
      file_count: 1,
      source_file_id: 'src',
      size: 100,
      created_at: '2026-11-06',
      source_available: true,
    };
    vi.mocked(api.listProjects).mockResolvedValue({ items: [project], total: 1 });
    vi.mocked(api.projectHistory).mockResolvedValue({
      history_id: 'project',
      items: [task()],
      total: 1,
      next_cursor: null,
      current_file: generated,
      active_task: null,
      sources: [],
    });
    vi.mocked(api.projectFiles).mockResolvedValue({ items: [] });
    vi.mocked(api.overwriteLocalTask).mockResolvedValue({
      path: localEdit.path,
      backup: '/Users/Sin/测试.docx.bak-20261106-180000',
      text: '已覆盖源文件，并保留备份。',
    });
    const confirm = vi
      .spyOn(ElMessageBox, 'confirm')
      .mockResolvedValue('confirm' as never);
    localStorage.setItem('office-agent-selected-project', 'project');

    const wrapper = mount(App);
    await flushPromises();
    await wrapper.get('[data-testid="overwrite-local"]').trigger('click');
    await flushPromises();

    expect(confirm).toHaveBeenCalled();
    expect(api.overwriteLocalTask).toHaveBeenCalledWith('task');
    expect(wrapper.get('[data-testid="local-file"]').text()).toContain(
      '已覆盖源文件',
    );
    wrapper.unmount();
  });
});
