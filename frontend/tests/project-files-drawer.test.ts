// @vitest-environment jsdom
import { mount, flushPromises } from '@vue/test-utils';
import { beforeEach, expect, it, vi } from 'vitest';
import { ElMessageBox } from 'element-plus';
import { deleteSourceFile, projectFiles } from '../src/api';
import ProjectFilesDrawer from '../src/components/ProjectFilesDrawer.vue';
import DocumentEditor from '../src/components/DocumentEditor.vue';

vi.mock('element-plus', async (importOriginal) => {
  const actual = await importOriginal<typeof import('element-plus')>();
  return { ...actual, ElMessageBox: { confirm: vi.fn(), prompt: vi.fn() } };
});
vi.mock('../src/api', () => ({
  deleteSourceFile: vi.fn(),
  projectFiles: vi.fn(),
  downloadUrl: (id: string) => `/api/files/${id}/download`,
  formatSize: () => '1 KB',
}));

const file = {
  id: 'one',
  name: '测试.docx',
  size: 10,
  created_at: '2026-10-29',
  available: true,
  origin: 'uploaded' as const,
  current_file: {
    id: 'latest',
    name: '结果.docx',
    size: 10,
    created_at: '2026-10-29',
  },
};

function setup(props = {}) {
  return mount(ProjectFilesDrawer, {
    attachTo: document.body,
    props: { projectId: 'project', selectable: true, selectedIds: [], ...props },
    global: { stubs: { DocumentEditor: true } },
  });
}
const body = <T extends Element = HTMLElement>(selector: string) =>
  document.body.querySelector(selector) as T | null;
async function click(selector: string) {
  const element = body<HTMLElement>(selector);
  expect(element, selector).not.toBeNull();
  element!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  await flushPromises();
}

beforeEach(() => {
  vi.clearAllMocks();
  document.body.innerHTML = '';
  vi.mocked(projectFiles).mockResolvedValue({ items: [file] });
});

it('renders a compact drawer with icon actions and opens preview in a dialog', async () => {
  const wrapper = setup();
  await flushPromises();
  expect(body('.files-drawer')).not.toBeNull();
  expect(document.body.textContent).toContain('项目文件');
  expect(document.body.textContent).toContain('上传');
  expect(document.body.querySelectorAll('.file-row')).toHaveLength(1);
  expect(body('[data-testid="source-download"]')?.getAttribute('href')).toBe(
    '/api/files/one/download',
  );

  await click('[data-testid="source-preview"]');
  expect(body('.preview-dialog')).not.toBeNull();
  expect(wrapper.findComponent(DocumentEditor).props('file').id).toBe('one');
  wrapper.findComponent(DocumentEditor).vm.$emit('close');
  await flushPromises();
  expect(document.body.querySelectorAll('.file-row')).toHaveLength(1);
  wrapper.unmount();
});

it('selects and deselects the current file version', async () => {
  const wrapper = setup();
  await flushPromises();
  body<HTMLInputElement>('.file-row input[type=checkbox]')!.click();
  await flushPromises();
  expect(wrapper.emitted('select')).toEqual([[file.current_file]]);

  await wrapper.setProps({ selectedIds: ['latest'] });
  await flushPromises();
  body<HTMLInputElement>('.file-row input[type=checkbox]')!.click();
  await flushPromises();
  expect(wrapper.emitted('deselect')).toEqual([['latest']]);
  wrapper.unmount();
});

it('deletes only after confirmation and removes the row on success', async () => {
  vi.mocked(ElMessageBox.confirm).mockResolvedValue('confirm' as never);
  vi.mocked(deleteSourceFile).mockResolvedValue({ id: 'one', deleted: true });
  const wrapper = setup();
  await flushPromises();
  await click('[data-testid="source-delete"]');
  expect(deleteSourceFile).toHaveBeenCalledWith('project', 'one');
  expect(document.body.querySelectorAll('.file-row')).toHaveLength(0);
  expect(wrapper.emitted('deleted')).toEqual([[file]]);
  wrapper.unmount();
});

it('keeps the row when deletion is cancelled or fails', async () => {
  vi.mocked(ElMessageBox.confirm).mockRejectedValue('cancel');
  const wrapper = setup();
  await flushPromises();
  await click('[data-testid="source-delete"]');
  expect(deleteSourceFile).not.toHaveBeenCalled();
  expect(document.body.querySelectorAll('.file-row')).toHaveLength(1);

  vi.mocked(ElMessageBox.confirm).mockResolvedValue('confirm' as never);
  vi.mocked(deleteSourceFile).mockRejectedValue(new Error('删除失败'));
  await click('[data-testid="source-delete"]');
  expect(document.body.textContent).toContain('删除失败');
  expect(document.body.querySelectorAll('.file-row')).toHaveLength(1);
  expect(wrapper.emitted('deleted')).toBeUndefined();
  wrapper.unmount();
});

it('disables selection and deletion while a task is active', async () => {
  const wrapper = setup({ busy: true });
  await flushPromises();
  expect(
    body<HTMLButtonElement>('.file-row input[type=checkbox]')?.disabled,
  ).toBe(true);
  expect(
    body<HTMLButtonElement>('[data-testid="source-delete"]')?.disabled,
  ).toBe(true);
  await click('[data-testid="source-delete"]');
  expect(ElMessageBox.confirm).not.toHaveBeenCalled();
  expect(deleteSourceFile).not.toHaveBeenCalled();
  wrapper.unmount();
});

it('shows an empty state when the project has no files', async () => {
  vi.mocked(projectFiles).mockResolvedValue({ items: [] });
  const wrapper = setup();
  await flushPromises();
  expect(document.body.textContent).toContain('还没有项目文件');
  expect(document.body.querySelectorAll('.file-row')).toHaveLength(0);
  wrapper.unmount();
});
