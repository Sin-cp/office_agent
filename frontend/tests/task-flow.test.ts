// @vitest-environment jsdom
import { mount, flushPromises } from '@vue/test-utils';
import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import App from '../src/App.vue';
import Composer from '../src/components/Composer.vue';
import FileDrawer from '../src/components/FileDrawer.vue';
import TaskProgress from '../src/components/TaskProgress.vue';
import * as api from '../src/api';
vi.mock('../src/api', async (original) => ({...await original<typeof api>(), listProjects:vi.fn(), uploadFile:vi.fn(), sendProjectTask:vi.fn(), getTask:vi.fn(), cancelTask:vi.fn(), projectHistory:vi.fn(), createProject:vi.fn()}));
const file = {id:'source',file_count:1,source_file_id:'source',source_available:true,name:'测试.docx',size:100,created_at:'2026-10-30'};
const newFile = {...file,id:'new',name:'测试_修改版.docx'};
const running: api.TaskState = {id:'task',project_id:'source',file,prompt:'润色开头',status:'running',stage:1,steps:['读取文档','生成修改内容','校验修改','生成文件'],elapsed_seconds:1,error:null,result:null};
const completed: api.TaskState = {...running,status:'completed',stage:3,elapsed_seconds:3,result:{file:newFile,position:1,summary:'已替换第 1 段',explanation:'正式表达',before:'旧',after:'新'}};
const history = (items: api.TaskState[] = [], current = file): api.FileHistory => ({history_id:file.id,sources:[{...file,current_file:current}],items,total:items.length,current_file:current,active_task:items.find(item => ['running','queued','cancelling'].includes(item.status)) || null,next_cursor:null});
beforeEach(() => {
  localStorage.clear(); vi.useFakeTimers(); vi.resetAllMocks();
  vi.mocked(api.listProjects).mockResolvedValue({items:[file],total:1});
  vi.mocked(api.createProject).mockResolvedValue({id:'source',name:'新项目'});
  vi.mocked(api.uploadFile).mockResolvedValue(file);
  vi.mocked(api.sendProjectTask).mockResolvedValue({...running});
  vi.mocked(api.projectHistory).mockResolvedValue(history());
});
afterEach(() => { vi.useRealTimers(); localStorage.clear(); });
async function ready() {
  localStorage.setItem('office-agent-selected-project','source');
  const wrapper = mount(App); await flushPromises();
  return wrapper;
}
it('keeps a new file on the home page and transitions only after task creation', async () => {
  const wrapper = await ready();
  expect(wrapper.find('h1').exists()).toBe(true);
  expect(wrapper.find('.conversation-history').exists()).toBe(false);
  expect(wrapper.get('.selected-file-name').text()).toBe(file.name);
  await wrapper.get('textarea').setValue('  ');
  expect(wrapper.get('.send-button').attributes('disabled')).toBeDefined();
  await wrapper.get('textarea').setValue('润色开头');
  await wrapper.get('.send-button').trigger('click'); await flushPromises();
  expect(api.sendProjectTask).toHaveBeenCalledWith(file.id,[file.id],'润色开头',true);
  expect(wrapper.find('h1').exists()).toBe(false);
  expect(wrapper.get('.user-message').text()).toContain('润色开头');
  expect(wrapper.get('textarea').element.value).toBe('');
  expect(wrapper.get('[aria-label="清除当前选中文件"]').attributes('disabled')).toBeDefined();
  expect(wrapper.get('.send-button').attributes('disabled')).toBeDefined();
  wrapper.unmount();
});
it('leaves the first prompt and home layout intact if creation fails', async () => {
  vi.mocked(api.sendProjectTask).mockRejectedValue(new Error('无法创建任务'));
  const wrapper = await ready();
  await wrapper.get('textarea').setValue('修改开头');
  await wrapper.get('.send-button').trigger('click'); await flushPromises();
  expect(wrapper.find('h1').exists()).toBe(true);
  expect(wrapper.get('textarea').element.value).toBe('修改开头');
  expect(wrapper.text()).toContain('无法创建任务');
  wrapper.unmount();
});
it('keeps conversation and draft when cancelling attachments and sends no files next', async () => {
  vi.mocked(api.projectHistory).mockResolvedValue(history([{...running,status:'failed',error:'模型响应超时'}]));
  const wrapper = await ready();
  await wrapper.get('textarea').setValue('草稿');
  await wrapper.get('[aria-label="清除当前选中文件"]').trigger('click');
  expect(wrapper.find('.conversation-history').exists()).toBe(true);
  expect(wrapper.find('.selected-file').exists()).toBe(false);
  expect(wrapper.get('textarea').element.value).toBe('草稿');
  expect(localStorage.getItem('office-agent-selected-project')).toBe('source');
  await wrapper.get('.send-button').trigger('click'); await flushPromises();
  expect(api.sendProjectTask).toHaveBeenCalledWith('source',[],'草稿',true);
  wrapper.unmount();
});
it('uses the completed version for the next turn and keeps both prompts', async () => {
  vi.mocked(api.getTask).mockResolvedValue(completed);
  const wrapper = await ready();
  await wrapper.get('textarea').setValue('润色开头');
  await wrapper.get('.send-button').trigger('click'); await flushPromises();
  await vi.advanceTimersByTimeAsync(1000); await flushPromises();
  expect(wrapper.get('.selected-file-name').text()).toBe(newFile.name);
  expect(wrapper.get('.task-actions a').attributes('href')).toContain('/new/download');
  expect(localStorage.getItem('office-agent-selected-project')).toBe('source');
  vi.mocked(api.sendProjectTask).mockResolvedValue({...running,id:'task2',file:newFile,prompt:'再精简一下'});
  await wrapper.get('textarea').setValue('再精简一下');
  await wrapper.get('.send-button').trigger('click'); await flushPromises();
  expect(api.sendProjectTask).toHaveBeenLastCalledWith('source',['new'],'再精简一下',true);
  expect(wrapper.findAll('.conversation-turn')).toHaveLength(2);
  wrapper.unmount();
});
it('scrolls to the newly sent message from older history without forcing later polling scrolls', async () => {
  vi.mocked(api.projectHistory).mockResolvedValue(history([completed],newFile));
  vi.mocked(api.sendProjectTask).mockResolvedValue({...running,id:'task2',file:newFile,prompt:'继续总结'});
  vi.mocked(api.getTask).mockResolvedValue({...completed,id:'task2',prompt:'继续总结'});
  const wrapper = await ready();
  const area = wrapper.get('.conversation-history').element as HTMLElement;
  let top = 100;
  // jsdom has no layout: emulate a scroller whose height changes after Vue renders the new turn.
  Object.defineProperties(area, {
    clientHeight: {get: () => 500},
    scrollHeight: {get: () => 1000 + area.querySelectorAll('.conversation-turn').length * 300},
    scrollTop: {get: () => top, set: (value: number) => { top = Math.max(0, Math.min(value, area.scrollHeight - area.clientHeight)); }},
  });
  await wrapper.get('textarea').setValue('继续总结');
  await wrapper.get('.send-button').trigger('click'); await flushPromises();
  expect(wrapper.findAll('.conversation-turn')).toHaveLength(2);
  expect(area.scrollTop).toBe(area.scrollHeight - area.clientHeight);

  area.scrollTop = 100; // The user returns to older messages while the task runs.
  await vi.advanceTimersByTimeAsync(1000); await flushPromises();
  expect(area.scrollTop).toBe(100);
  wrapper.unmount();
});
it('restores selected file and active history then survives polling network failure', async () => {
  localStorage.setItem('office-agent-selected-project','source');
  vi.mocked(api.projectHistory).mockResolvedValue(history([running]));
  vi.mocked(api.getTask).mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(completed);
  const wrapper = mount(App); await flushPromises();
  expect(wrapper.find('.conversation-history').exists()).toBe(true);
  await vi.advanceTimersByTimeAsync(1000); await flushPromises();
  expect(wrapper.text()).toContain('暂时无法获取进度');
  expect(wrapper.get('.send-button').attributes('disabled')).toBeDefined();
  await vi.advanceTimersByTimeAsync(1000); await flushPromises();
  expect(wrapper.text()).toContain('修改完成');
  expect(wrapper.text()).toContain('3.0 秒');
  wrapper.unmount();
});
it('does not guess empty history while loading and blocks sending on error until retry', async () => {
  let reject!: (cause: Error) => void;
  vi.mocked(api.projectHistory).mockReturnValueOnce(new Promise((_, no) => {reject=no;}));
  const wrapper = await ready();
  expect(wrapper.text()).toContain('正在加载文件历史');
  expect(wrapper.find('h1').exists()).toBe(false);
  reject(new Error('offline')); await flushPromises();
  expect(wrapper.find('.selected-file-name').exists()).toBe(false);
  await wrapper.get('textarea').setValue('修改');
  expect(wrapper.get('.send-button').attributes('disabled')).toBeDefined();
  await wrapper.get('.history-status button').trigger('click'); await flushPromises();
  expect(wrapper.find('h1').exists()).toBe(true);
  expect(wrapper.get('.send-button').attributes('disabled')).toBeUndefined();
  wrapper.unmount();
});
it('waits for cancellation confirmation before allowing removal', async () => {
  vi.mocked(api.projectHistory).mockResolvedValue(history([running]));
  vi.mocked(api.cancelTask).mockResolvedValue({...running,status:'cancelling'});
  vi.mocked(api.getTask).mockResolvedValue({...running,status:'cancelled'});
  const wrapper = await ready();
  wrapper.getComponent(TaskProgress).vm.$emit('stop'); await flushPromises();
  expect(wrapper.get('[aria-label="清除当前选中文件"]').attributes('disabled')).toBeDefined();
  await vi.advanceTimersByTimeAsync(1000); await flushPromises();
  expect(wrapper.get('[aria-label="清除当前选中文件"]').attributes('disabled')).toBeUndefined();
  expect(wrapper.get('.selected-file-name').text()).toBe(file.name);
  wrapper.unmount();
});
it('switches files without mixing records and restores the newest version on reopening', async () => {
  vi.mocked(api.projectHistory).mockResolvedValueOnce(history([completed],newFile));
  const wrapper = await ready();
  expect(wrapper.get('.selected-file-name').text()).toBe(newFile.name);
  await wrapper.get('.files-toggle').trigger('click'); await flushPromises();
  const other = {...file,id:'other',name:'另一份.docx'};
  vi.mocked(api.projectHistory).mockResolvedValueOnce(history([],other));
  wrapper.getComponent(FileDrawer).vm.$emit('edit',other); await flushPromises();
  expect(wrapper.find('.conversation-history').exists()).toBe(false);
  expect(wrapper.get('.selected-file-name').text()).toBe(other.name);
  wrapper.unmount();
});
it('loads older history without duplication and retains deleted results without links', async () => {
  const removed = {...completed,id:'old',result:{...completed.result!,file:{...newFile,available:false}}};
  vi.mocked(api.projectHistory).mockResolvedValueOnce({...history([completed],newFile),next_cursor:'task'}).mockResolvedValueOnce({...history([removed,completed],newFile),next_cursor:null});
  const wrapper = await ready();
  await wrapper.get('.older-messages').trigger('click'); await flushPromises();
  expect(wrapper.findAll('.conversation-turn')).toHaveLength(2);
  expect(wrapper.findAll('.task-actions a')).toHaveLength(1);
  expect(wrapper.text()).toContain('此文件已删除或不可用');
  wrapper.unmount();
});
it('shows failure reason without a download link', () => {
  const wrapper = mount(TaskProgress,{props:{task:{...running,status:'failed',error:'模型响应超时'},stopping:false}});
  expect(wrapper.text()).toContain('失败阶段：生成修改内容');
  expect(wrapper.find('a').exists()).toBe(false);
  wrapper.unmount();
});
it('keeps one source project after completion and clears current project by history identity', async () => {
  vi.mocked(api.projectHistory).mockResolvedValue(history([completed],newFile));
  const wrapper = await ready();
  expect(wrapper.text()).not.toContain('已替换第 1 段');
  expect(wrapper.text()).not.toContain('修改说明');
  expect(wrapper.text()).toContain('查看修改内容');
  await wrapper.get('textarea').setValue('下一轮');
  await wrapper.get('.files-toggle').trigger('click'); await flushPromises();
  expect(wrapper.getComponent(FileDrawer).props('projects')).toEqual([file]);
  wrapper.getComponent(FileDrawer).vm.$emit('deleted',{...file,id:'other'});
  await flushPromises();
  expect(wrapper.get('textarea').element.value).toBe('下一轮');
  wrapper.getComponent(FileDrawer).vm.$emit('deleted',file); await flushPromises();
  expect(wrapper.find('.conversation-history').exists()).toBe(false);
  expect(wrapper.get('textarea').element.value).toBe('');
  expect(localStorage.getItem('office-agent-selected-project')).toBeNull();
  wrapper.unmount();
});

it('renders an answer without modification chrome, file controls, or changing the selected version', async () => {
  const answer: api.TaskState = {...running, status:'completed', result:{kind:'answer',text:'这是文档总结。',citations:[{file_id:file.id,block_id:'source:p:0',location:'正文第1段',quote:'原文'}]}};
  vi.mocked(api.getTask).mockResolvedValue(answer);
  const wrapper = await ready();
  await wrapper.get('textarea').setValue('总结文档');
  await wrapper.get('.send-button').trigger('click'); await flushPromises();
  await vi.advanceTimersByTimeAsync(1000); await flushPromises();
  expect(wrapper.text()).toContain('这是文档总结。');
  expect(wrapper.text()).not.toContain('修改完成');
  expect(wrapper.find('.task-actions').exists()).toBe(false);
  expect(wrapper.find('.task-heading').exists()).toBe(false);
  expect(wrapper.find('.task-progress ol').exists()).toBe(false);
  expect(wrapper.get('.selected-file-name').text()).toBe(file.name);
  expect(localStorage.getItem('office-agent-selected-project')).toBe(file.id);
  wrapper.unmount();
});

it('renders clarification as a message and keeps batch differences separate', () => {
  const wrapper = mount(TaskProgress,{props:{stopping:false,task:{...running,status:'completed',result:{kind:'clarify',text:'你指的是哪个章节？'}}}});
  expect(wrapper.text()).toContain('你指的是哪个章节？');
  expect(wrapper.find('a').exists()).toBe(false);
  wrapper.unmount();
  const batch = mount(TaskProgress,{props:{stopping:false,task:{...completed,result:{kind:'edit',text:'已修改两处',file:newFile,changes:[
    {op:'replace',block_id:'a',location:'正文第1段',before:'旧',after:'新'},
    {op:'insert_paragraph',block_id:'b',location:'正文第2段',before:'',after:'新增'},
  ]}}}});
  expect(batch.findAll('.task-result section')).toHaveLength(2);
  expect(batch.text()).toContain('新增');
  expect(batch.find('.task-actions a').exists()).toBe(true);
  batch.unmount();
});

it('removes version recovery and citation UI', async () => {
  vi.mocked(api.projectHistory).mockResolvedValue(history([completed],newFile));
  const wrapper = await ready();
  expect(wrapper.find('.version-toolbar').exists()).toBe(false);
  expect(wrapper.text()).not.toContain('原文依据');
  expect(wrapper.text()).not.toContain('阅读范围');
  wrapper.unmount();
});
it('starts and restores a pure chat without file controls', async () => {
  const reply: api.TaskState = {...completed,file:null,files:[],result:{kind:'answer',text:'你好，我是助手'}};
  vi.mocked(api.sendProjectTask).mockResolvedValue(reply);
  const wrapper = mount(App); await flushPromises();
  await wrapper.get('textarea').setValue('你好');
  await wrapper.get('.send-button').trigger('click'); await flushPromises();
  expect(api.sendProjectTask).toHaveBeenCalledWith(null,[],'你好',true);
  expect(wrapper.text()).toContain('你好，我是助手');
  expect(wrapper.text()).not.toContain('项目文件');
  expect(wrapper.find('.task-actions').exists()).toBe(false);
  wrapper.unmount();
  vi.mocked(api.projectHistory).mockResolvedValue({...history([reply]),sources:[]});
  const reopened = mount(App); await flushPromises();
  expect(reopened.text()).toContain('你好，我是助手');
  expect(reopened.find('.selected-file').exists()).toBe(false);
  reopened.unmount();
});
it('renders a separate result and download for every modified file', () => {
  const wrapper = mount(TaskProgress,{props:{stopping:false,task:{...completed,result:{kind:'edit',text:'两份文件已修改',outputs:[{file:newFile,changes:[]},{file:{...newFile,id:'other'},changes:[]}]}}}});
  expect(wrapper.findAll('.task-actions a').map(a=>a.attributes('href'))).toEqual(['/api/files/new/download','/api/files/other/download']);
  wrapper.unmount();
});
it('keeps cancelled attachment selection after reopening the project', async () => {
  vi.mocked(api.projectHistory).mockResolvedValue(history([completed],newFile));
  const wrapper = await ready();
  await wrapper.get('[aria-label="清除当前选中文件"]').trigger('click');
  wrapper.unmount();
  const reopened = mount(App); await flushPromises();
  expect(reopened.find('.selected-file').exists()).toBe(false);
  expect(reopened.find('.conversation-history').exists()).toBe(true);
  expect(reopened.find('.composer-dock > button').exists()).toBe(true);
  reopened.unmount();
});
it('sends the web switch value and remembers it after reload', async () => {
  const wrapper = mount(App); await flushPromises();
  expect(wrapper.get('[role="switch"]').attributes('aria-checked')).toBe('true');
  await wrapper.get('[role="switch"]').trigger('click');
  expect(wrapper.get('[role="switch"]').attributes('aria-checked')).toBe('false');
  await wrapper.get('textarea').setValue('今天的天气');
  await wrapper.get('.send-button').trigger('click'); await flushPromises();
  expect(api.sendProjectTask).toHaveBeenCalledWith(null,[],'今天的天气',false);
  wrapper.unmount();
  const reopened = mount(App); await flushPromises();
  expect(reopened.get('[role="switch"]').attributes('aria-checked')).toBe('false');
  reopened.unmount();
});
it('restores clickable web sources and rejects unsafe links', async () => {
  const sources: api.WebSource[] = [
    {title:'天气资料',url:'https://example.com/weather',retrieved_at:'2026-11-03T08:00:00+08:00',kind:'page'},
    {title:'不安全链接',url:'javascript:alert(1)',retrieved_at:'2026-11-03T08:00:00+08:00',kind:'search'},
  ];
  vi.mocked(api.projectHistory).mockResolvedValue({...history([{...completed,result:{kind:'answer',text:'天气查询结果',web_sources:sources}}]),sources:[]});
  const wrapper = await ready();
  expect(wrapper.get('.web-sources a').attributes('href')).toBe(sources[0]!.url);
  expect(wrapper.findAll('.web-sources a')).toHaveLength(1);
  expect(wrapper.get('.web-sources a').attributes('rel')).toBe('noopener noreferrer');
  expect(wrapper.text()).not.toContain('原文依据');
  wrapper.unmount();
});
it('shows retrieval activity and search failure without claiming verification', () => {
  const wrapper = mount(TaskProgress,{props:{stopping:false,task:{...running,web_activity:'正在搜索'}}});
  expect(wrapper.get('[role="status"]').text()).toContain('正在搜索');
  wrapper.unmount();
  const failed = mount(TaskProgress,{props:{stopping:false,task:{...completed,result:{kind:'answer',text:'无法核实',web_status:'failed',web_notices:[{code:'timeout',message:'联网查询超时'}],web_sources:[]}}}});
  expect(failed.text()).toContain('联网查询超时');
  expect(failed.find('.web-sources').exists()).toBe(false);
  failed.unmount();
});

it('new document keeps existing attachments, joins selection and has creation controls', async () => {
  const wrapper = await ready();
  const created: api.TaskState = {...completed,result:{kind:'create',text:'已保存到当前项目',file:{...newFile,source_file_id:null},outputs:[{file:{...newFile,source_file_id:null},changes:[]}]}};
  vi.mocked(api.getTask).mockResolvedValue(created);
  await wrapper.get('textarea').setValue('新建一份文档');
  await wrapper.get('.send-button').trigger('click'); await flushPromises();
  await vi.advanceTimersByTimeAsync(1000); await flushPromises();
  expect(wrapper.text()).toContain('文档已生成');
  expect(wrapper.text()).not.toContain('查看修改内容');
  expect(wrapper.findAll('.selected-file-name')).toHaveLength(2);
  expect(wrapper.findAll('.task-actions a')).toHaveLength(1);
  expect(JSON.parse(localStorage.getItem('office-agent-attachments:source')!)).toEqual(['source','new']);
  wrapper.unmount();
});
it('shows repair progress only while a task is active', () => {
  const wrapper = mount(TaskProgress, {props:{task:{...running,activity:'正在补充遗漏内容（1/2）',repair_round:1},stopping:false}});
  expect(wrapper.get('[role=status]').text()).toContain('正在补充遗漏内容（1/2）');
  wrapper.unmount();
  const finished = mount(TaskProgress, {props:{task:{...completed,activity:'正在检查修改完整性',repair_round:1},stopping:false}});
  expect(finished.find('[role=status]').exists()).toBe(false);
  finished.unmount();
});
