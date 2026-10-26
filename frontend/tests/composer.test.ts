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
