// @vitest-environment jsdom
import { mount, flushPromises } from '@vue/test-utils';
import { beforeEach, expect, it, vi } from 'vitest';
import { ElMessageBox } from 'element-plus';
import { deleteProject, renameProject } from '../src/api';
import FileDrawer from '../src/components/FileDrawer.vue';
vi.mock('element-plus', () => ({ ElMessageBox: { confirm: vi.fn(), prompt: vi.fn() } }));
vi.mock('../src/api', () => ({ deleteProject: vi.fn(), renameProject: vi.fn(), downloadUrl: () => '/download', formatSize: () => '1 KB' }));
const file = { id: 'one', file_count: 1, source_file_id: 'one', source_available: true, name: '测试.docx', size: 10, created_at: '2026-10-29' };
const setup = () => mount(FileDrawer, { props: { projects: [file], total: 1, loading: false, error: '' }, global: { stubs: { teleport: true } } });
beforeEach(() => vi.clearAllMocks());
it('does not delete on cancel', async () => {
  vi.mocked(ElMessageBox.confirm).mockRejectedValue('cancel');
  const wrapper = setup(); await wrapper.get('.delete-file').trigger('click'); await flushPromises();
  expect(deleteProject).not.toHaveBeenCalled(); expect(wrapper.emitted('deleted')).toBeUndefined(); wrapper.unmount();
});
it('deletes after confirmation and reports success', async () => {
  vi.mocked(ElMessageBox.confirm).mockResolvedValue('confirm');
  vi.mocked(deleteProject).mockResolvedValue({ id: 'one', deleted: true });
  const wrapper = setup(); await wrapper.get('.delete-file').trigger('click'); await flushPromises();
  expect(deleteProject).toHaveBeenCalledWith('one'); expect(wrapper.emitted('deleted')).toEqual([[file]]); wrapper.unmount();
});
it('keeps the file and displays API failure', async () => {
  vi.mocked(ElMessageBox.confirm).mockResolvedValue('confirm');
  vi.mocked(deleteProject).mockRejectedValue(new Error('删除失败'));
  const wrapper = setup(); await wrapper.get('.delete-file').trigger('click'); await flushPromises();
  expect(wrapper.get('[role=alert]').text()).toBe('删除失败'); expect(wrapper.emitted('deleted')).toBeUndefined(); wrapper.unmount();
});
it('shows source list and continue chat without direct download; hides file controls for chat', async () => {
  const wrapper = setup();
  expect(wrapper.text()).toContain('项目管理');
  expect(wrapper.text()).toContain('项目文件');
  expect(wrapper.text()).toContain('继续聊天');
  expect(wrapper.find('a').exists()).toBe(false);
  await wrapper.setProps({projects:[{...file,file_count:0,source_available:false}]});
  expect(wrapper.text()).not.toContain('项目文件');
  expect(wrapper.text()).toContain('继续聊天');
  wrapper.unmount();
});
it('renames a project and refreshes only after save', async () => {
  vi.mocked(ElMessageBox.prompt).mockResolvedValue({value:'新的标题',action:'confirm'});
  vi.mocked(renameProject).mockResolvedValue({id:'one',name:'新的标题'});
  const wrapper = setup();
  await wrapper.get('.rename-project').trigger('click'); await flushPromises();
  expect(renameProject).toHaveBeenCalledWith('one','新的标题');
  expect(wrapper.emitted('renamed')).toHaveLength(1);
  wrapper.unmount();
});
it('blocks deletion while execution is active', async () => {
  const wrapper = setup();
  await wrapper.setProps({busy:true});
  await wrapper.get('.delete-file').trigger('click');
  expect(ElMessageBox.confirm).not.toHaveBeenCalled();
  expect(deleteProject).not.toHaveBeenCalled();
  wrapper.unmount();
});
