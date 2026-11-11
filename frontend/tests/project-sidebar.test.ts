// @vitest-environment jsdom
import { mount, flushPromises } from '@vue/test-utils';
import { beforeEach, expect, it, vi } from 'vitest';
import { ElDropdown, ElMessageBox } from 'element-plus';
import { deleteProject, renameProject } from '../src/api';
import ProjectSidebar from '../src/components/ProjectSidebar.vue';

vi.mock('element-plus', async (importOriginal) => {
  const actual = await importOriginal<typeof import('element-plus')>();
  return { ...actual, ElMessageBox: { confirm: vi.fn(), prompt: vi.fn() } };
});
vi.mock('../src/api', () => ({
  deleteProject: vi.fn(),
  renameProject: vi.fn(),
}));

const project = {
  id: 'one',
  file_count: 2,
  source_file_id: 'one',
  source_available: true,
  name: '测试项目',
  size: 10,
  created_at: new Date().toISOString(),
};

type Props = Partial<{
  projects: (typeof project)[];
  total: number;
  activeProjectId: string | null;
  busy: boolean;
  loading: boolean;
  error: string;
}>;

function setup(props: Props = {}) {
  return mount(ProjectSidebar, {
    props: {
      projects: [project],
      total: 1,
      loading: false,
      error: '',
      ...props,
    },
  });
}

async function command(wrapper: ReturnType<typeof setup>, value: string) {
  wrapper.findAllComponents(ElDropdown)[0]!.vm.$emit('command', value);
  await flushPromises();
}

beforeEach(() => {
  vi.clearAllMocks();
});

it('renders the DSH-style header, new session and clickable project rows', async () => {
  const wrapper = setup({ activeProjectId: 'one' });
  await flushPromises();
  expect(wrapper.text()).toContain('OfficeAgent');
  expect(wrapper.text()).toContain('v1.0.0');
  expect(wrapper.get('.new-session-button').text()).toContain('新会话');
  expect(wrapper.text()).not.toContain('继续聊天');
  expect(wrapper.get('.project-item').classes()).toContain('active');
  expect(wrapper.get('.project-select').attributes('aria-current')).toBe('page');

  await wrapper.get('.project-select').trigger('click');
  expect(wrapper.emitted('select')).toEqual([[project]]);

  await wrapper.get('.new-session-button').trigger('click');
  expect(wrapper.emitted('new-session')).toHaveLength(1);

  await wrapper.get('[aria-label="收起项目面板"]').trigger('click');
  expect(wrapper.emitted('collapse')).toHaveLength(1);
  wrapper.unmount();
});

it('opens project files from the row menu', async () => {
  const wrapper = setup();
  await command(wrapper, 'files');
  expect(wrapper.emitted('view')).toEqual([[project]]);
  wrapper.unmount();
});

it('renames a project and refreshes only after save', async () => {
  vi.mocked(ElMessageBox.prompt).mockResolvedValue({
    value: '新的标题',
    action: 'confirm',
  } as never);
  vi.mocked(renameProject).mockResolvedValue({ id: 'one', name: '新的标题' });
  const wrapper = setup();
  await command(wrapper, 'rename');
  expect(renameProject).toHaveBeenCalledWith('one', '新的标题');
  expect(wrapper.emitted('renamed')).toHaveLength(1);
  wrapper.unmount();
});

it('does not delete when the confirmation is cancelled', async () => {
  vi.mocked(ElMessageBox.confirm).mockRejectedValue('cancel');
  const wrapper = setup();
  await command(wrapper, 'delete');
  expect(deleteProject).not.toHaveBeenCalled();
  expect(wrapper.emitted('deleted')).toBeUndefined();
  wrapper.unmount();
});

it('deletes after confirmation and reports success', async () => {
  vi.mocked(ElMessageBox.confirm).mockResolvedValue('confirm' as never);
  vi.mocked(deleteProject).mockResolvedValue({ id: 'one', deleted: true });
  const wrapper = setup();
  await command(wrapper, 'delete');
  expect(deleteProject).toHaveBeenCalledWith('one');
  expect(wrapper.emitted('deleted')).toEqual([[project]]);
  wrapper.unmount();
});

it('keeps the project and displays an API failure', async () => {
  vi.mocked(ElMessageBox.confirm).mockResolvedValue('confirm' as never);
  vi.mocked(deleteProject).mockRejectedValue(new Error('删除失败'));
  const wrapper = setup();
  await command(wrapper, 'delete');
  expect(wrapper.text()).toContain('删除失败');
  expect(wrapper.emitted('deleted')).toBeUndefined();
  wrapper.unmount();
});

it('blocks selection, renaming and deletion while a task is active', async () => {
  const wrapper = setup({ busy: true });
  expect(wrapper.get('.project-select').attributes('disabled')).toBeDefined();
  expect(wrapper.get('.project-more').attributes('disabled')).toBeDefined();
  await command(wrapper, 'delete');
  expect(ElMessageBox.confirm).not.toHaveBeenCalled();
  expect(deleteProject).not.toHaveBeenCalled();
  wrapper.unmount();
});

it('shows loading, empty and error states with retry hooks', async () => {
  const loading = setup({ projects: [], total: 0, loading: true });
  expect(loading.find('.project-skeleton').exists()).toBe(true);
  loading.unmount();

  const empty = setup({ projects: [], total: 0 });
  expect(empty.text()).toContain('还没有项目');
  empty.unmount();

  const failed = setup({ projects: [], total: 0, error: '无法加载' });
  expect(failed.text()).toContain('无法加载');
  await failed.get('.list-retry button').trigger('click');
  expect(failed.emitted('retry')).toHaveLength(1);
  failed.unmount();
});

it('offers load more when the server has additional projects', async () => {
  const wrapper = setup({ total: 3 });
  expect(wrapper.get('.load-more').text()).toContain('加载更多');
  await wrapper.get('.load-more').trigger('click');
  expect(wrapper.emitted('more')).toHaveLength(1);
  wrapper.unmount();
});
