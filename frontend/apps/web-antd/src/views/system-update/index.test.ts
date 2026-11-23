// @vitest-environment happy-dom

import { createApp, defineComponent, h, nextTick, ref } from 'vue';
import type { App } from 'vue';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
  getSystemUpdateApi: vi.fn(),
  startSystemUpdateApi: vi.fn(),
  getSystemUpdateJobApi: vi.fn(),
}));
const permissions = ref<string[]>([]);

vi.mock('#/api', () => api);
vi.mock('@vben/access', () => ({
  useAccess: () => ({ hasAccessByCodes: (codes: string[]) => codes.every((code) => permissions.value.includes(code)) }),
}));
vi.mock('@vben/common-ui', () => ({
  Page: defineComponent({ setup: (_, { slots }) => () => h('main', slots.default?.()) }),
}));
vi.mock('ant-design-vue', async () => {
  const { defineComponent, h } = await import('vue');
  const container = defineComponent({ inheritAttrs: false, setup: (_, { slots }) => () => h('div', slots.default?.()) });
  const button = defineComponent({ props: ['loading'], emits: ['click'], setup: (props, { emit, slots }) => () => h('button', { disabled: props.loading, onClick: () => emit('click') }, slots.default?.()) });
  const list = defineComponent({ props: ['dataSource'], setup: (props, { slots }) => () => h('div', props.dataSource?.map((item: unknown) => slots.renderItem?.({ item })) ) });
  const meta = defineComponent({ props: ['title', 'description'], setup: (props) => () => h('div', [h('div', props.title), h('div', props.description)]) });
  const description = Object.assign(container, { Item: container });
  return {
    Alert: defineComponent({ props: ['message'], setup: (props) => () => h('div', { role: 'alert' }, props.message) }),
    Button: button, Card: container, Descriptions: description, List: Object.assign(list, { Item: Object.assign(container, { Meta: meta }) }),
    Space: container, Spin: container,
    message: { success: vi.fn(), error: vi.fn() },
    Progress: defineComponent({ props: ['percent'], setup: (props) => () => h('progress', { value: props.percent }) }),
    Modal: defineComponent({ props: ['open', 'title'], emits: ['ok'], setup: (props, { emit, slots }) => () => props.open ? h('section', [h('h2', props.title), slots.default?.(), h('button', { onClick: () => emit('ok') }, '开始更新')]) : null }),
    Tag: defineComponent({ props: ['color'], setup: (_, { slots }) => () => h('span', slots.default?.()) }),
  };
});

import SystemUpdatePage from './index.vue';

const update = {
  currentVersion: '0.1.0', latestVersion: '0.1.1', updateAvailable: true,
  tag: 'v0.1.1', publishedAt: '2026-11-23T00:00:00Z',
  releaseUrl: 'https://github.com/shiyi-log/fuwu/releases/tag/v0.1.1',
  notes: '<script>alert(1)</script>\n修复更新检查。',
  assets: [{ name: 'fuwu.tar.gz', size: 2048, digest: 'a'.repeat(64) }],
};

let app: App | undefined;
let host: HTMLDivElement;
async function flush() { for (let index = 0; index < 8; index += 1) await Promise.resolve(); await nextTick(); }
async function mount() { host = document.createElement('div'); document.body.append(host); app = createApp(SystemUpdatePage); app.mount(host); await flush(); }

beforeEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
  permissions.value = ['fuwu.update.manage'];
  api.getSystemUpdateApi.mockResolvedValue(update);
});
afterEach(() => { app?.unmount(); app = undefined; host?.remove(); });

describe('system update page', () => {
  it('loads update data, renders notes as text, assets, and a validated GitHub link', async () => {
    await mount();
    expect(api.getSystemUpdateApi).toHaveBeenCalledOnce();
    expect(host.textContent).toContain('有可用更新');
    expect(host.textContent).toContain('可从此页面提交线上更新任务');
    expect(host.textContent).toContain('<script>alert(1)</script>');
    expect(host.textContent).toContain('fuwu.tar.gz');
    expect(host.querySelector('a')?.getAttribute('href')).toBe(update.releaseUrl);
    expect(host.querySelector('script')).toBeNull();
  });

  it('does not call the update endpoint without the management permission', async () => {
    permissions.value = [];
    await mount();
    expect(api.getSystemUpdateApi).not.toHaveBeenCalled();
    expect(host.textContent).toContain('当前账号没有系统更新权限');
  });

  it('does not render a non-GitHub release URL', async () => {
    api.getSystemUpdateApi.mockResolvedValue({ ...update, releaseUrl: 'javascript:alert(1)' });
    await mount();
    expect(host.querySelector('a')).toBeNull();
    expect(host.textContent).toContain('未返回有效 GitHub Release 地址');
  });

  it('does not render a release URL for another GitHub repository', async () => {
    api.getSystemUpdateApi.mockResolvedValue({
      ...update,
      releaseUrl: 'https://github.com/attacker/fuwu/releases/tag/v0.1.1',
    });
    await mount();
    expect(host.querySelector('a')).toBeNull();
  });

  it('shows assets whose upstream digest is not available', async () => {
    api.getSystemUpdateApi.mockResolvedValue({
      ...update,
      assets: [{ name: 'source.tar.gz', size: 1024, digest: null }],
    });
    await mount();
    expect(host.textContent).toContain('source.tar.gz');
    expect(host.textContent).toContain('SHA-256 未提供');
  });

  it('submits the latest tag after confirmation and polls the persisted job', async () => {
    const created = {
      jobId: 'job-1', status: 'queued', progress: 0, message: '排队中', errorCode: null,
      fromVersion: '0.1.0', toVersion: '0.1.1', startedAt: null, updatedAt: null,
    };
    api.startSystemUpdateApi.mockResolvedValue(created);
    api.getSystemUpdateJobApi.mockResolvedValue({ ...created, status: 'succeeded', progress: 100, message: '完成' });
    await mount();
    const updateButton = [...host.querySelectorAll('button')].find((button) => button.textContent?.includes('在线更新')) as HTMLButtonElement;
    updateButton.click();
    await flush();
    expect(host.textContent).toContain('确认在线更新');
    const confirmButton = [...host.querySelectorAll('button')].find((button) => button.textContent?.includes('开始更新')) as HTMLButtonElement;
    confirmButton.click();
    await flush();
    expect(api.startSystemUpdateApi).toHaveBeenCalledWith('v0.1.1');
    await vi.waitFor(() => expect(api.getSystemUpdateJobApi).toHaveBeenCalledWith('job-1'));
    expect(api.getSystemUpdateJobApi).toHaveBeenCalledWith('job-1');
    expect(sessionStorage.getItem('fuwu.system-update.job-id')).toBeNull();
    expect(host.textContent).toContain('更新成功');
  });

  it('restores a queued job after a page refresh', async () => {
    sessionStorage.setItem('fuwu.system-update.job-id', 'job-2');
    api.getSystemUpdateJobApi.mockResolvedValue({
      jobId: 'job-2', status: 'downloading', progress: 42, message: '下载中', errorCode: null,
      fromVersion: '0.1.0', toVersion: '0.1.1', startedAt: null, updatedAt: null,
    });
    await mount();
    expect(api.getSystemUpdateJobApi).toHaveBeenCalledWith('job-2');
    expect(host.textContent).toContain('下载中');
    expect(sessionStorage.getItem('fuwu.system-update.job-id')).toBe('job-2');
  });

  it('offers a retry when the task status request fails', async () => {
    const current = {
      jobId: 'job-3', status: 'downloading', progress: 42, message: '下载中', errorCode: null,
      fromVersion: '0.1.0', toVersion: '0.1.1', startedAt: null, updatedAt: null,
    } as const;
    sessionStorage.setItem('fuwu.system-update.job-id', current.jobId);
    api.getSystemUpdateJobApi.mockRejectedValueOnce(new Error('network')).mockResolvedValueOnce(current);
    await mount();
    await vi.waitFor(() => expect(host.textContent).toContain('重新查询任务'));
    const retryButton = [...host.querySelectorAll('button')].find((button) => button.textContent?.includes('重新查询任务')) as HTMLButtonElement;
    retryButton.click();
    await flush();
    expect(api.getSystemUpdateJobApi).toHaveBeenCalledTimes(2);
    expect(host.textContent).toContain('下载中');
  });
});
