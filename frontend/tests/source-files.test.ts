// @vitest-environment jsdom
import { mount, flushPromises } from '@vue/test-utils';
import { beforeEach, expect, it, vi } from 'vitest';
import { projectFiles, deleteSourceFile } from '../src/api';
import SourceFilesDialog from '../src/components/SourceFilesDialog.vue';
vi.mock('../src/api', () => ({ projectFiles: vi.fn(), deleteSourceFile: vi.fn(), downloadUrl: () => '/download', formatSize: () => '1 KB' }));
const file = { id: 'one', name: '测试.docx', size: 10, created_at: '2026-10-29', available: true, current_file: { id: 'latest', name: '结果.docx', size: 10, created_at: '2026-10-29' } };
beforeEach(() => { vi.clearAllMocks(); vi.mocked(projectFiles).mockResolvedValue({ items: [file] }); });
const setup = async () => { const wrapper = mount(SourceFilesDialog, { props: { projectId: 'project' }, global: { stubs: { teleport: true, DocumentEditor: true } } }); await flushPromises(); return wrapper; };
it('confirms removal and updates only after successful deletion', async () => {
  const wrapper = await setup();
  expect(wrapper.findAll('.source-table .source-action')).toHaveLength(3);
  await wrapper.get('.source-table .source-delete').trigger('click');
  expect(deleteSourceFile).not.toHaveBeenCalled();
  await wrapper.get('.source-delete-confirm .source-preview').trigger('click');
  expect(deleteSourceFile).not.toHaveBeenCalled();
  await wrapper.get('.source-table .source-delete').trigger('click');
  vi.mocked(deleteSourceFile).mockResolvedValue({ id: 'one', deleted: true });
  await wrapper.get('.source-delete-confirm .source-delete').trigger('click'); await flushPromises();
  expect(deleteSourceFile).toHaveBeenCalledWith('project', 'one');
  expect(wrapper.emitted('deleted')).toEqual([[file]]);
  expect(wrapper.text()).toContain('项目文件（0）');
  expect(wrapper.emitted('close')).toBeUndefined(); wrapper.unmount();
});
it('keeps source on errors and disables deletion during tasks', async () => {
  const wrapper = await setup();
  vi.mocked(deleteSourceFile).mockRejectedValue(new Error('删除失败'));
  await wrapper.get('.source-table .source-delete').trigger('click');
  await wrapper.get('.source-delete-confirm .source-delete').trigger('click'); await flushPromises();
  expect(wrapper.get('[role=alert]').text()).toContain('删除失败');
  expect(wrapper.text()).toContain('项目文件（1）');
  expect(wrapper.emitted('deleted')).toBeUndefined();
  await wrapper.setProps({ busy: true });
  expect(wrapper.get('.source-table .source-delete').attributes('disabled')).toBeDefined(); wrapper.unmount();
});
