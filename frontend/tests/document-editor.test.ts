// @vitest-environment jsdom
import { mount, flushPromises } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import DocumentEditor from '../src/components/DocumentEditor.vue';
import { renderAsync } from 'docx-preview';
vi.mock('docx-preview', () => ({ renderAsync: vi.fn() }));
const file = { id: 'source', name: '方案.docx', size: 1, created_at: '2026' };
beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, arrayBuffer: async () => new ArrayBuffer(1) }));
  vi.mocked(renderAsync).mockImplementation(async (_, container) => {
    container.innerHTML = '<section class="docx"><h1>原标题</h1><table><tr><td>表格内容</td></tr></table></section>';
    return {} as never;
  });
});
afterEach(() => vi.unstubAllGlobals());
describe('document preview', () => {
  it('renders document pages in an isolated frame without search or block labels', async () => {
    const wrapper = mount(DocumentEditor, { props: { file } });
    await flushPromises();
    const frame = wrapper.get('iframe');
    expect(frame.attributes('srcdoc')).toContain('表格内容');
    expect(frame.attributes('sandbox')).toBe('');
    expect(wrapper.find('input').exists()).toBe(false);
    expect(wrapper.find('.block-label').exists()).toBe(false);
    expect(fetch).toHaveBeenCalledWith('/api/files/source/download', expect.any(Object));
    await wrapper.get('.viewer-actions button').trigger('click'); await flushPromises();
    expect(renderAsync).toHaveBeenCalledTimes(2);
    await wrapper.get('.viewer-actions button:last-child').trigger('click');
    expect(wrapper.emitted('close')).toHaveLength(1);
    wrapper.unmount();
  });
  it('displays reading errors without a stale preview', async () => {
    vi.mocked(fetch).mockResolvedValue({ ok: false, json: async () => ({ detail: '文件不存在' }) } as Response);
    const wrapper = mount(DocumentEditor, { props: { file } }); await flushPromises();
    expect(wrapper.get('[role=alert]').text()).toContain('文件不存在');
    expect(wrapper.find('iframe').exists()).toBe(false);
    wrapper.unmount();
  });
});
