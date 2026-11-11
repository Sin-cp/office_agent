// @vitest-environment jsdom
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';
import Composer from '../src/components/Composer.vue';
import type { FileInfo, SourceFile } from '../src/api';

function source(id: string, name: string, extra: Partial<SourceFile> = {}): SourceFile {
  const file: FileInfo = {
    id: `file-${id}`,
    name,
    size: 38_000,
    created_at: '2026-11-06T06:04:00Z',
    available: true,
  };
  return {
    id,
    name,
    size: file.size,
    created_at: file.created_at,
    available: true,
    origin: 'uploaded',
    current_file: file,
    ...extra,
  };
}

const guide = source('a', '北京元旦三日游攻略.docx');
const plan = source('b', '2027元旦北京三日游攻略.docx');

function setup(overrides: Record<string, unknown> = {}) {
  let wrapper: VueWrapper;
  wrapper = mount(Composer, {
    props: {
      busy: false,
      message: '',
      error: '',
      modelValue: '',
      'onUpdate:modelValue': (value: string) => {
        void wrapper.setProps({ modelValue: value });
      },
      ...overrides,
    },
  });
  return wrapper;
}

function textarea(wrapper: VueWrapper) {
  return wrapper.get('textarea');
}

describe('composer @ document mentions', () => {
  it('lists every project document and marks the currently attached ones', async () => {
    const wrapper = setup({
      files: [guide.current_file],
      sourceFiles: [guide, plan],
    });
    await textarea(wrapper).setValue('@');
    await flushPromises();

    expect(wrapper.find('.mention-popover').exists()).toBe(true);
    expect(wrapper.findAll('.mention-row')).toHaveLength(2);
    expect(
      wrapper.get('[data-testid="mention-toggle-a"]').attributes('aria-pressed'),
    ).toBe('true');
    expect(
      wrapper.get('[data-testid="mention-toggle-b"]').attributes('aria-pressed'),
    ).toBe('false');
    expect(wrapper.emitted('refresh-sources')).toHaveLength(1);
    wrapper.unmount();
  });

  it('filters while typing, inserts with Enter, and keeps the picker open for multi-select', async () => {
    const wrapper = setup({ files: [], sourceFiles: [guide, plan] });
    await textarea(wrapper).setValue('@');
    await flushPromises();
    await textarea(wrapper).setValue('@2027');
    await flushPromises();
    expect(wrapper.findAll('.mention-row')).toHaveLength(1);

    await textarea(wrapper).trigger('keydown', { key: 'Enter' });
    await flushPromises();

    expect(wrapper.emitted('attach')?.[0]).toEqual([plan.current_file]);
    expect(textarea(wrapper).element.value).toContain('@2027元旦北京三日游攻略.docx');
    expect(wrapper.find('.mention-popover').exists()).toBe(true);

    await wrapper.setProps({ files: [plan.current_file] });
    await flushPromises();
    expect(wrapper.findAll('.mention-token')).toHaveLength(1);
    expect(wrapper.get('.mention-token').text()).toBe('@2027元旦北京三日游攻略.docx');
    wrapper.unmount();
  });

  it('supports arrow navigation, Tab completion, and multiple mentions', async () => {
    const wrapper = setup({ files: [], sourceFiles: [guide, plan] });
    await textarea(wrapper).setValue('@');
    await flushPromises();

    await textarea(wrapper).trigger('keydown', { key: 'ArrowDown' });
    await textarea(wrapper).trigger('keydown', { key: 'Tab' });
    await flushPromises();
    expect(wrapper.emitted('attach')?.[0]).toEqual([plan.current_file]);
    expect(textarea(wrapper).element.value).toContain('@2027元旦北京三日游攻略.docx');

    await wrapper.setProps({ files: [plan.current_file] });
    await textarea(wrapper).trigger('keydown', { key: 'Enter' });
    await flushPromises();
    expect(wrapper.emitted('attach')?.[1]).toEqual([guide.current_file]);
    expect(textarea(wrapper).element.value).toContain('@北京元旦三日游攻略.docx');
    expect(wrapper.findAll('.mention-row')).toHaveLength(2);
    wrapper.unmount();
  });

  it('does not open for an e-mail address and closes with Escape before Enter sends', async () => {
    const wrapper = setup({ sourceFiles: [guide] });
    await textarea(wrapper).setValue('hello@');
    await flushPromises();
    expect(wrapper.find('.mention-popover').exists()).toBe(false);

    await textarea(wrapper).setValue('@');
    await flushPromises();
    expect(wrapper.find('.mention-popover').exists()).toBe(true);
    await textarea(wrapper).trigger('keydown', { key: 'Escape' });
    await flushPromises();
    expect(wrapper.find('.mention-popover').exists()).toBe(false);

    await textarea(wrapper).trigger('keydown', { key: 'Enter' });
    expect(wrapper.emitted('send')).toHaveLength(1);
    wrapper.unmount();
  });

  it('inserts a mention for an attached but unmentioned document instead of detaching it', async () => {
    const wrapper = setup({
      files: [guide.current_file],
      sourceFiles: [guide],
    });
    await textarea(wrapper).setValue('@');
    await flushPromises();
    await textarea(wrapper).trigger('keydown', { key: 'Enter' });
    await flushPromises();

    expect(wrapper.emitted('clear')).toBeUndefined();
    expect(textarea(wrapper).element.value).toContain('@北京元旦三日游攻略.docx');
    wrapper.unmount();
  });

  it('removes the echoed mention when a file is unchecked or its chip is dismissed', async () => {
    const wrapper = setup({
      files: [guide.current_file],
      sourceFiles: [guide],
      modelValue: '请修改 @北京元旦三日游攻略.docx 的标题',
    });
    await textarea(wrapper).setValue('@');
    await flushPromises();
    await wrapper.get('[data-testid="mention-toggle-a"]').trigger('click');
    await flushPromises();
    expect(wrapper.emitted('clear')?.[0]).toEqual(['file-a']);
    expect(textarea(wrapper).element.value).toBe('');

    await wrapper.setProps({
      files: [guide.current_file],
      modelValue: '请修改 @北京元旦三日游攻略.docx 的标题',
    });
    await wrapper.get('[aria-label="清除当前选中文件"]').trigger('click');
    await flushPromises();
    expect(wrapper.emitted('clear')?.[1]).toEqual(['file-a']);
    expect(textarea(wrapper).element.value).toBe('请修改 的标题');
    wrapper.unmount();
  });

  it('closes the picker as soon as normal text is typed after inserting a mention', async () => {
    const wrapper = setup({ files: [], sourceFiles: [guide] });
    await textarea(wrapper).setValue('@');
    await flushPromises();
    await textarea(wrapper).trigger('keydown', { key: 'Enter' });
    await flushPromises();
    const inserted = textarea(wrapper).element.value;
    expect(inserted).toContain('@北京元旦三日游攻略.docx');

    await textarea(wrapper).setValue(`${inserted}帮我看看`);
    await flushPromises();
    expect(wrapper.find('.mention-popover').exists()).toBe(false);
    wrapper.unmount();
  });
});

it('keeps the mention highlighted while native IME composition text is visible', async () => {
  const wrapper = setup({
    files: [guide.current_file],
    sourceFiles: [guide],
    modelValue: '@北京元旦三日游攻略.docx 我想调整',
  });
  const area = textarea(wrapper).element as HTMLTextAreaElement;

  await textarea(wrapper).trigger('compositionstart');
  await textarea(wrapper).trigger('compositionupdate', { data: 'ni' });
  area.value = `${area.value}ni`;
  area.dispatchEvent(new Event('input', { bubbles: true }));
  await flushPromises();

  expect(wrapper.get('.composer-editor').classes()).toContain('is-composing');
  expect(wrapper.get('.composer-highlight').text()).toContain('ni');
  expect(wrapper.get('.mention-token').text()).toBe('@北京元旦三日游攻略.docx');
  expect(wrapper.get('.composing-token').text()).toBe('ni');

  await textarea(wrapper).trigger('compositionend');
  await wrapper.setProps({ modelValue: area.value });
  await flushPromises();
  expect(wrapper.find('.composing-token').exists()).toBe(false);
  expect(wrapper.get('.mention-token').text()).toBe('@北京元旦三日游攻略.docx');
  wrapper.unmount();
});
