// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { describe, it, expect } from 'vitest'
import Composer from '../src/components/Composer.vue'

function setup(busy = false) {
  return mount(Composer, { props: { busy, message: '', error: '' } })
}
const file = () => new File(['test'], '测试.docx')

describe('composer', () => {
  it('uploads one dropped file and clears the drop overlay', async () => {
    const wrapper = setup()
    await wrapper.get('section').trigger('dragenter')
    expect(wrapper.find('.drop-overlay').exists()).toBe(true)
    const selected = file()
    await wrapper.get('section').trigger('drop', { dataTransfer: { files: [selected] } })
    expect(wrapper.emitted('upload')).toEqual([[selected]])
    expect(wrapper.find('.drop-overlay').exists()).toBe(false)
  })
  it('rejects multiple files, empty files, wrong extensions and excessive size', async () => {
    const wrapper = setup()
    const large = file()
    Object.defineProperty(large, 'size', { value: 10 * 1024 * 1024 + 1 })
    for (const files of [[file(), file()], [new File([], 'empty.docx')], [new File(['x'], 'text.txt')], [large]]) {
      await wrapper.get('section').trigger('drop', { dataTransfer: { files } })
    }
    expect(wrapper.emitted('invalid')).toHaveLength(4)
    expect(wrapper.emitted('upload')).toBeUndefined()
  })
  it('does not upload again while busy and never sends draft text', async () => {
    const wrapper = setup(true)
    await wrapper.get('section').trigger('drop', { dataTransfer: { files: [file()] } })
    await wrapper.get('textarea').setValue('修改第二个标题\n保持格式')
    expect(wrapper.emitted('upload')).toBeUndefined()
    expect(wrapper.get('.send-button').attributes('disabled')).toBeDefined()
    expect(wrapper.get('textarea').element.value).toContain('保持格式')
  })
})


describe('composer keyboard sending', () => {
  const selectedFile = { id: 'one', name: '测试.docx', size: 10, created_at: '2026-10-30' }
  function ready(overrides = {}) {
    return mount(Composer, { props: { busy: false, message: '', error: '', file: selectedFile, modelValue: '修改正文', ...overrides } })
  }
  function press(wrapper: ReturnType<typeof ready>, options: KeyboardEventInit = {}) {
    const event = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true, ...options })
    wrapper.get('textarea').element.dispatchEvent(event)
    return event
  }
  it('sends on Enter and prevents a newline', () => {
    const wrapper = ready()
    expect(press(wrapper).defaultPrevented).toBe(true)
    expect(wrapper.emitted('send')).toHaveLength(1)
    wrapper.unmount()
  })
  it('leaves Shift+Enter to the native textarea newline behavior', () => {
    const wrapper = ready()
    expect(press(wrapper, { shiftKey: true }).defaultPrevented).toBe(false)
    expect(wrapper.emitted('send')).toBeUndefined()
    wrapper.unmount()
  })
  it('does not send when missing a file or prompt, disabled, busy, or holding Enter', () => {
    for (const props of [{ file: undefined }, { modelValue: '  ' }, { busy: true }, { sendDisabled: true }]) {
      const wrapper = ready(props)
      press(wrapper)
      expect(wrapper.emitted('send')).toBeUndefined()
      wrapper.unmount()
    }
    const wrapper = ready()
    press(wrapper, { repeat: true })
    expect(wrapper.emitted('send')).toBeUndefined()
    wrapper.unmount()
  })
  it('does not submit when Enter is used to confirm an IME candidate', async () => {
    const wrapper = ready()
    expect(press(wrapper, { isComposing: true }).defaultPrevented).toBe(false)
    expect(press(wrapper, { keyCode: 229 }).defaultPrevented).toBe(false)
    await wrapper.get('textarea').trigger('compositionstart')
    expect(press(wrapper).defaultPrevented).toBe(false)
    expect(wrapper.emitted('send')).toBeUndefined()
    await wrapper.get('textarea').trigger('compositionend')
    press(wrapper)
    expect(wrapper.emitted('send')).toHaveLength(1)
    wrapper.unmount()
  })
})
