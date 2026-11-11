// @vitest-environment jsdom
import { mount, flushPromises } from '@vue/test-utils';
import { beforeEach, expect, it, vi } from 'vitest';
import { ElMessageBox } from 'element-plus';
import App from '../src/App.vue';
import * as api from '../src/api';

vi.mock('element-plus', async (original) => ({
  ...await original<typeof import('element-plus')>(),
  ElMessageBox: { confirm: vi.fn(), prompt: vi.fn() },
}));
vi.mock('../src/api', async (original) => ({
  ...await original<typeof api>(),
  listProjects: vi.fn(),
  projectFiles: vi.fn(),
  projectHistory: vi.fn(),
  sendProjectTask: vi.fn(),
  getTask: vi.fn(),
  cancelTask: vi.fn(),
  createProject: vi.fn(),
  uploadFile: vi.fn(),
  deleteTask: vi.fn(),
}));

const turn = (id: string, prompt: string, text: string): api.TaskState => ({
  id,
  project_id: 'project',
  file: null,
  prompt,
  status: 'completed',
  stage: 3,
  steps: ['读取会话', '理解请求', '处理内容', '保存结果'],
  elapsed_seconds: 1,
  error: null,
  result: { kind: 'answer', text },
});

const history = (items: api.TaskState[]): api.FileHistory => ({
  history_id: 'project',
  sources: [],
  items,
  total: items.length,
  next_cursor: null,
  current_file: null,
  active_task: null,
});

async function ready(items: api.TaskState[]) {
  localStorage.setItem('office-agent-selected-project', 'project');
  vi.mocked(api.listProjects).mockResolvedValue({ items: [], total: 0 });
  vi.mocked(api.projectFiles).mockResolvedValue({ items: [] });
  vi.mocked(api.projectHistory).mockResolvedValue(history(items));
  const wrapper = mount(App);
  await flushPromises();
  return wrapper;
}

beforeEach(() => {
  localStorage.clear();
  vi.resetAllMocks();
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText: vi.fn().mockResolvedValue(undefined) },
  });
});

const rows = (wrapper: ReturnType<typeof mount>) => wrapper.findAll('.conversation-turn');

it('copies a prompt and an answer from their own action row', async () => {
  const wrapper = await ready([turn('t1', '第一轮问题', '第一轮回答')]);
  const row = rows(wrapper)[0]!;

  await row.get('[data-testid="turn-copy"]').trigger('click');
  await flushPromises();
  expect(navigator.clipboard.writeText).toHaveBeenCalledWith('第一轮问题');
  expect(row.get('.prompt-actions').text()).toContain('已复制');

  await row.findAll('[data-testid="turn-copy"]')[1]!.trigger('click');
  await flushPromises();
  expect(navigator.clipboard.writeText).toHaveBeenLastCalledWith('第一轮回答');
  wrapper.unmount();
});

it('deletes a whole turn only after the confirmation', async () => {
  vi.mocked(ElMessageBox.confirm).mockRejectedValueOnce('cancel');
  const wrapper = await ready([turn('t1', '要删除的问题', '要删除的回答')]);
  await rows(wrapper)[0]!.get('[data-testid="turn-delete"]').trigger('click');
  await flushPromises();
  expect(ElMessageBox.confirm).toHaveBeenCalledTimes(1);
  expect(api.deleteTask).not.toHaveBeenCalled();
  expect(rows(wrapper)).toHaveLength(1);

  vi.mocked(ElMessageBox.confirm).mockResolvedValueOnce('confirm' as never);
  vi.mocked(api.deleteTask).mockResolvedValue({ id: 't1', deleted: true });
  await rows(wrapper)[0]!.get('[data-testid="turn-delete"]').trigger('click');
  await flushPromises();
  expect(api.deleteTask).toHaveBeenCalledWith('t1');
  expect(rows(wrapper)).toHaveLength(0);
  expect(wrapper.text()).not.toContain('要删除的问题');
  wrapper.unmount();
});

it('reports a failed delete and keeps the turn', async () => {
  vi.mocked(ElMessageBox.confirm).mockResolvedValueOnce('confirm' as never);
  vi.mocked(api.deleteTask).mockRejectedValue(new Error('任务正在执行，请先停止再删除'));
  const wrapper = await ready([turn('t1', '第一轮问题', '第一轮回答')]);
  await rows(wrapper)[0]!.get('[data-testid="turn-delete"]').trigger('click');
  await flushPromises();
  expect(rows(wrapper)).toHaveLength(1);
  expect(wrapper.text()).toContain('任务正在执行，请先停止再删除');
  wrapper.unmount();
});

it('regenerates with the same prompt as a new turn', async () => {
  const wrapper = await ready([turn('t1', '再问一次的问题', '第一轮回答')]);
  vi.mocked(api.sendProjectTask).mockResolvedValue({
    ...turn('t2', '再问一次的问题', ''),
    status: 'running',
    result: null,
  } as api.TaskState);
  await rows(wrapper)[0]!.get('[data-testid="turn-regenerate"]').trigger('click');
  await flushPromises();
  expect(api.sendProjectTask).toHaveBeenCalledWith('project', [], '再问一次的问题', true, []);
  expect(rows(wrapper)).toHaveLength(2);
  wrapper.unmount();
});
