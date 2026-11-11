// @vitest-environment jsdom
import { mount, flushPromises } from '@vue/test-utils';
import { expect, it, vi } from 'vitest';
import App from '../src/App.vue';
import Composer from '../src/components/Composer.vue';
import ProjectFilesDrawer from '../src/components/ProjectFilesDrawer.vue';
import DocumentEditor from '../src/components/DocumentEditor.vue';
import * as api from '../src/api';
vi.mock('../src/api', async original => ({...await original<typeof api>(), createProject:vi.fn(), projectFiles:vi.fn(), projectHistory:vi.fn(), listProjects:vi.fn(), uploadFile:vi.fn(), readDocument:vi.fn()}));
const fileRows = () => document.body.querySelectorAll('.file-row');
it('appends multiple files to one project, retains draft on removal, and previews source list in a drawer', async () => {
  localStorage.clear();
  const file = {id:'one',name:'测试.docx',size:100,created_at:'2026-10-29',available:true};
  const second = {...file,id:'two'};
  vi.mocked(api.listProjects).mockResolvedValue({items:[],total:0});
  vi.mocked(api.createProject).mockResolvedValue({id:'project',name:'新项目'});
  vi.mocked(api.uploadFile).mockResolvedValueOnce(file).mockResolvedValueOnce(second);
  vi.mocked(api.projectFiles).mockResolvedValue({items:[{...file,current_file:file},{...second,current_file:second}]});
  vi.mocked(api.readDocument).mockResolvedValue({file,revision:'sha',blocks:[],notice:'',table_count:0});
  const wrapper = mount(App); await flushPromises();
  await wrapper.get('textarea').setValue('修改标题');
  wrapper.getComponent(Composer).vm.$emit('upload',[new File(['x'],'a.docx'),new File(['x'],'b.docx')]);
  await flushPromises();
  expect(api.createProject).toHaveBeenCalledTimes(1);
  expect(api.uploadFile).toHaveBeenCalledTimes(2);
  expect(vi.mocked(api.uploadFile).mock.calls.every(c=>c[1]==='project')).toBe(true);
  expect(wrapper.findAll('.selected-file')).toHaveLength(2);
  await wrapper.findAll('[aria-label="清除当前选中文件"]')[0]!.trigger('click');
  expect(wrapper.get('textarea').element.value).toBe('修改标题');
  expect(wrapper.findAll('.selected-file')).toHaveLength(1);
  await wrapper.get('[data-testid="project-files"]').trigger('click'); await flushPromises();
  const drawer = wrapper.getComponent(ProjectFilesDrawer);
  expect(fileRows()).toHaveLength(2);
  expect([...document.body.querySelectorAll('[data-testid="source-download"]')].map(a=>a.getAttribute('href'))).toEqual(['/api/files/one/download','/api/files/two/download']);
  const preview = document.body.querySelector('[data-testid="source-preview"]') as HTMLButtonElement;
  preview.click(); await flushPromises();
  expect(drawer.getComponent(DocumentEditor).props('file').id).toBe('one');
  drawer.getComponent(DocumentEditor).vm.$emit('close'); await flushPromises();
  expect(fileRows()).toHaveLength(2);
  drawer.vm.$emit('close'); await flushPromises();
  expect(wrapper.get('textarea').element.value).toBe('修改标题');
  wrapper.unmount(); localStorage.clear();
});

it('collapses and restores the project sidebar from the top bar', async () => {
  localStorage.clear();
  vi.mocked(api.listProjects).mockResolvedValue({items:[],total:0});
  const wrapper = mount(App); await flushPromises();
  expect(wrapper.get('.sidebar-shell').classes()).toContain('is-open');
  expect(wrapper.find('[aria-label="展开项目面板"]').exists()).toBe(false);
  await wrapper.get('[aria-label="收起项目面板"]').trigger('click'); await flushPromises();
  expect(wrapper.get('.sidebar-shell').classes()).not.toContain('is-open');
  expect(wrapper.get('.sidebar-shell').attributes('aria-hidden')).toBe('true');
  expect(wrapper.find('[aria-label="展开项目面板"]').exists()).toBe(true);
  expect(wrapper.get('.workspace-bar').classes()).toContain('is-visible');
  expect(localStorage.getItem('office-agent-sidebar-open')).toBe('false');
  await wrapper.get('[aria-label="展开项目面板"]').trigger('click'); await flushPromises();
  expect(wrapper.get('.sidebar-shell').classes()).toContain('is-open');
  expect(wrapper.get('.workspace-bar').classes()).not.toContain('is-visible');
  expect(localStorage.getItem('office-agent-sidebar-open')).toBe('true');
  wrapper.unmount(); localStorage.clear();
});

it('loads the project files when @ opens the mention picker', async () => {
  localStorage.clear();
  const file = {
    id: 'one',
    name: '北京元旦三日游攻略.docx',
    size: 100,
    created_at: '2026-10-29',
    available: true,
  };
  const source = { ...file, origin: 'uploaded' as const, current_file: file };
  const project = {
    id: 'project',
    name: '测试项目',
    file_count: 1,
    source_file_id: 'one',
    size: 100,
    created_at: '2026-10-29',
    source_available: true,
  };
  vi.mocked(api.listProjects).mockResolvedValue({ items: [project], total: 1 });
  vi.mocked(api.projectHistory).mockResolvedValue({
    history_id: 'project',
    items: [],
    total: 0,
    next_cursor: null,
    current_file: null,
    active_task: null,
    sources: [source],
  });
  vi.mocked(api.projectFiles).mockResolvedValue({ items: [source] });
  localStorage.setItem('office-agent-selected-project', 'project');
  localStorage.setItem('office-agent-attachments:project', '[]');

  const wrapper = mount(App);
  await flushPromises();
  await wrapper.get('textarea').setValue('@');
  await flushPromises();

  expect(api.projectFiles).toHaveBeenCalledWith('project');
  expect(wrapper.find('.mention-popover').exists()).toBe(true);
  expect(wrapper.findAll('.mention-row')).toHaveLength(1);
  expect(wrapper.get('.mention-row').text()).toContain(file.name);
  wrapper.unmount();
  localStorage.clear();
});
