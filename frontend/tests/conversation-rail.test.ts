// @vitest-environment jsdom
import { mount } from '@vue/test-utils';
import { expect, it, vi } from 'vitest';
import ConversationRail from '../src/components/ConversationRail.vue';

function stage(turns: number) {
  const host = document.createElement('div');
  host.className = 'conversation-history';
  host.style.overflowY = 'auto';
  turns && host.setAttribute('data-turn-count', String(turns));
  const items = Array.from({ length: turns }, (_, index) => {
    const article = document.createElement('article');
    article.className = 'conversation-turn';
    article.dataset.taskId = `t${index + 1}`;
    article.getBoundingClientRect = () =>
      ({ top: index * 200, bottom: index * 200 + 180, height: 180, left: 0, right: 600, width: 600, x: 0, y: index * 200, toJSON: () => ({}) }) as DOMRect;
    host.appendChild(article);
    return article;
  });
  host.getBoundingClientRect = () =>
    ({ top: 0, bottom: 400, height: 400, left: 0, right: 600, width: 600, x: 0, y: 0, toJSON: () => ({}) }) as DOMRect;
  document.body.appendChild(host);
  return { host, items };
}

const turnsFor = (count: number) =>
  Array.from({ length: count }, (_, index) => ({
    id: `t${index + 1}`,
    prompt: `第 ${index + 1} 轮的问题`,
  }));

it('numbers one marker per turn and hides a single-turn conversation', async () => {
  const single = mount(ConversationRail, { props: { turns: turnsFor(1), scrollArea: null } });
  expect(single.find('.turn-rail').exists()).toBe(false);
  single.unmount();

  const { host } = stage(3);
  const wrapper = mount(ConversationRail, {
    props: { turns: turnsFor(3), scrollArea: host },
    attachTo: document.body,
  });
  expect(wrapper.findAll('.turn-rail-item')).toHaveLength(3);
  expect(wrapper.get('.turn-rail').attributes('aria-label')).toBe('对话锚点');
  wrapper.unmount();
  host.remove();
});

it('reveals the prompt text on hover and marks the active turn', async () => {
  const { host } = stage(3);
  const wrapper = mount(ConversationRail, {
    props: { turns: turnsFor(3), scrollArea: host },
    attachTo: document.body,
  });
  const items = wrapper.findAll('.turn-rail-item');
  expect(items[0]!.attributes('aria-label')).toBe('第 1 轮：第 1 轮的问题');
  expect(wrapper.find('.rail-tip').exists()).toBe(false);

  await items[0]!.trigger('mouseenter');
  await wrapper.vm.$nextTick();
  expect(wrapper.get('.rail-tip').text()).toBe('第 1 轮的问题');

  await items[0]!.trigger('mouseleave');
  await wrapper.vm.$nextTick();
  expect(wrapper.find('.rail-tip').exists()).toBe(false);

  await items[2]!.trigger('mouseenter');
  await wrapper.vm.$nextTick();
  expect(wrapper.get('.rail-tip').text()).toBe('第 3 轮的问题');
  wrapper.unmount();
  host.remove();
});

it('scrolls the history to the clicked turn', async () => {
  const { host } = stage(3);
  const wrapper = mount(ConversationRail, {
    props: { turns: turnsFor(3), scrollArea: host },
    attachTo: document.body,
  });
  await wrapper.vm.$nextTick();
  await wrapper.findAll('.turn-rail-item')[1]!.trigger('click');
  expect(host.scrollTo).toHaveBeenCalledWith(
    expect.objectContaining({ top: 188, behavior: 'smooth' }),
  );
  expect(wrapper.findAll('.turn-rail-item')[1]!.attributes('aria-current')).toBe('true');
  wrapper.unmount();
  host.remove();
});

it('follows scrolling to keep the active marker in sync', async () => {
  const { host, items } = stage(3);
  const wrapper = mount(ConversationRail, {
    props: { turns: turnsFor(3), scrollArea: host },
    attachTo: document.body,
  });
  items[1]!.getBoundingClientRect = () =>
    ({ top: 20, bottom: 200, height: 180, left: 0, right: 600, width: 600, x: 0, y: 20, toJSON: () => ({}) }) as DOMRect;
  items[2]!.getBoundingClientRect = () =>
    ({ top: 260, bottom: 440, height: 180, left: 0, right: 600, width: 600, x: 0, y: 260, toJSON: () => ({}) }) as DOMRect;
  host.dispatchEvent(new Event('scroll'));
  await new Promise((resolve) => setTimeout(resolve, 40));
  await wrapper.vm.$nextTick();
  expect(wrapper.findAll('.turn-rail-item')[1]!.classes()).toContain('is-active');
  wrapper.unmount();
  host.remove();
});
