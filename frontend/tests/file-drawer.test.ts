// @vitest-environment jsdom
import { mount, flushPromises } from '@vue/test-utils';
import { beforeEach, expect, it, vi } from 'vitest';
import { ElMessageBox } from 'element-plus';
import { deleteFile } from '../src/api';
import FileDrawer from '../src/components/FileDrawer.vue';
vi.mock('element-plus', () => ({ ElMessageBox: { confirm: vi.fn() } }));
vi.mock('../src/api', () => ({ deleteFile: vi.fn(), downloadUrl: () => '/download', formatSize: () => '1 KB' }));
const file = { id: 'one', name: '测试.docx', size: 10, created_at: '2026-10-29' };
const setup = () => mount(FileDrawer, { props: { files: [file], total: 1, loading: false, error: '' }, global: { stubs: { teleport: true } } });
beforeEach(() => vi.clearAllMocks());
it('does not delete on cancel', async () => {
  vi.mocked(ElMessageBox.confirm).mockRejectedValue('cancel');
  const wrapper = setup(); await wrapper.get('.delete-file').trigger('click'); await flushPromises();
  expect(deleteFile).not.toHaveBeenCalled(); expect(wrapper.emitted('deleted')).toBeUndefined(); wrapper.unmount();
});
it('deletes after confirmation and reports success', async () => {
  vi.mocked(ElMessageBox.confirm).mockResolvedValue('confirm');
  vi.mocked(deleteFile).mockResolvedValue({ id: 'one', deleted: true });
  const wrapper = setup(); await wrapper.get('.delete-file').trigger('click'); await flushPromises();
  expect(deleteFile).toHaveBeenCalledWith('one'); expect(wrapper.emitted('deleted')).toEqual([[file]]); wrapper.unmount();
});
it('keeps the file and displays API failure', async () => {
  vi.mocked(ElMessageBox.confirm).mockResolvedValue('confirm');
  vi.mocked(deleteFile).mockRejectedValue(new Error('删除失败'));
  const wrapper = setup(); await wrapper.get('.delete-file').trigger('click'); await flushPromises();
  expect(wrapper.get('[role=alert]').text()).toBe('删除失败'); expect(wrapper.emitted('deleted')).toBeUndefined(); wrapper.unmount();
});
