import { createApp, defineComponent, h, nextTick, reactive, ref } from 'vue';
import type { App } from 'vue';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
  getLatestGitHubReleaseApi: vi.fn(), getReleaseAuditApi: vi.fn(), getReleaseListApi: vi.fn(),
  getReleaseStorageStatusApi: vi.fn(), publishReleaseApi: vi.fn(), rollbackReleaseApi: vi.fn(),
  stageRemoteReleaseApi: vi.fn(), syncLatestGitHubReleaseApi: vi.fn(),
}));
const messages = vi.hoisted(() => ({ success: vi.fn(), warning: vi.fn(), error: vi.fn() }));
const route = reactive({ meta: { projectId: 'heyue', projectName: 'Heyue' } });
const permissions = ref<string[]>([]);
vi.mock('#/api', () => api);
vi.mock('vue-router', () => ({ useRoute: () => route }));
vi.mock('@vben/access', () => ({ useAccess: () => ({
  hasAccessByCodes: (codes: string[]) => codes.every((code) => permissions.value.includes(code)),
}) }));
vi.mock('@vben/common-ui', () => ({
  Page: defineComponent({ setup: (_, { slots }) => () => h('main', slots.default?.()) }),
}));
vi.mock('ant-design-vue', async () => {
  const { defineComponent, h } = await import('vue');
  const container = defineComponent({ inheritAttrs: false, setup: (_, { slots }) => () => h('div', slots.default?.()) });
  const input = defineComponent({ props: ['value'], emits: ['update:value'],
    setup: (props, { emit }) => () => h('input', { value: props.value,
      onInput: (event: Event) => emit('update:value', (event.target as HTMLInputElement).value) }),
  });
  const select = defineComponent({ props: ['value'], emits: ['update:value'],
    setup: (props, { emit, slots }) => () => h('select', { value: props.value,
      onChange: (event: Event) => emit('update:value', (event.target as HTMLSelectElement).value) }, slots.default?.()),
  });
  const option = defineComponent({ props: ['value'], setup: (props, { slots }) => () => h('option', { value: props.value }, slots.default?.()) });
  return {
    Alert: defineComponent({ props: ['message'], setup: (props) => () => h('div', { role: 'alert' }, props.message) }),
    Button: defineComponent({ inheritAttrs: false, props: ['disabled', 'loading'], emits: ['click'],
      setup: (props, { emit, slots }) => () => h('button', { disabled: props.disabled || props.loading, onClick: () => emit('click') }, slots.default?.()),
    }),
    Card: container, Form: Object.assign(container, { Item: container }), Input: Object.assign(input, { TextArea: input }),
    Modal: defineComponent({ props: ['open'], setup: (props, { slots }) => () => props.open ? h('section', slots.default?.()) : null }),
    Progress: container, Select: Object.assign(select, { Option: option }), Space: container, Spin: container, Tag: container,
    Table: defineComponent({ props: ['dataSource'], setup: (props) => () => h('pre', JSON.stringify(props.dataSource)) }), message: messages,
  };
});
import ReleasePage from './index.vue';
function latest(project = 'heyue', version = '1.0.14') {
  return { project, repository: `example/${project}`, version, tag: `v${version}`, revision: 'a'.repeat(40),
    assets: ['app.tar.gz'], publishedAt: null, manifest: {}, releaseId: 'release-1' };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}
async function flush() {
  for (let index = 0; index < 12; index += 1) await Promise.resolve();
  await nextTick();
}
let app: App | undefined;
let host: HTMLDivElement;
async function mount() {
  host = document.createElement('div'); document.body.append(host);
  app = createApp(ReleasePage); app.mount(host); await flush();
}
function button(text: string) {
  const found = [...host.querySelectorAll('button')].find((element) => element.textContent?.trim() === text);
  if (!found) throw new Error(`Button not found: ${text}`);
  return found;
}
beforeEach(() => {
  vi.useFakeTimers(); vi.clearAllMocks();
  for (const mock of Object.values(api)) mock.mockReset();
  route.meta.projectId = 'heyue'; route.meta.projectName = 'Heyue';
  permissions.value = ['release.storage.read', 'release.storage.publish'];
  api.getReleaseListApi.mockResolvedValue([]); api.getReleaseStorageStatusApi.mockResolvedValue({ reachable: true });
  api.getReleaseAuditApi.mockResolvedValue([]); api.getLatestGitHubReleaseApi.mockResolvedValue(latest());
  api.syncLatestGitHubReleaseApi.mockResolvedValue({ status: 'already_published', latest: latest() });
});
afterEach(() => { app?.unmount(); app = undefined; host?.remove(); vi.useRealTimers(); });

describe('发行页面自动同步', () => {
  it.each([
    ['published', '已自动同步 v1.0.14 到 stable'],
    ['already_published', 'Fuwu 已包含 GitHub 最新正式版 v1.0.14'],
    ['current_newer', '已跳过降级'],
  ])('displays %s and reloads authoritative release records', async (status, label) => {
    api.syncLatestGitHubReleaseApi.mockResolvedValue({ status, latest: latest() });
    api.getReleaseListApi.mockResolvedValue([{ project_id: 'heyue', version: '1.0.15', channel: 'stable', latest: true }]);
    await mount();
    expect(api.syncLatestGitHubReleaseApi).toHaveBeenCalledExactlyOnceWith('heyue', 'stable');
    expect(api.getReleaseListApi).toHaveBeenCalledTimes(2); expect(host.textContent).toContain(label);
    expect(messages.success).toHaveBeenCalledTimes(status === 'published' ? 1 : 0);
  });
  it('lets a scoped reader discover metadata without publishing', async () => {
    permissions.value = ['release.storage.read']; await mount();
    expect(api.getLatestGitHubReleaseApi).toHaveBeenCalledExactlyOnceWith('heyue');
    expect(api.syncLatestGitHubReleaseApi).not.toHaveBeenCalled();
    expect(host.textContent).toContain('尚未发布到 stable'); expect(host.textContent).not.toContain('发布版本');
    button('重新读取').click(); await flush();
    expect(api.getLatestGitHubReleaseApi).toHaveBeenCalledTimes(2); expect(api.syncLatestGitHubReleaseApi).not.toHaveBeenCalled();
  });
  it('does not discover or synchronize without release read permission', async () => {
    permissions.value = []; await mount();
    expect(api.getLatestGitHubReleaseApi).not.toHaveBeenCalled(); expect(api.syncLatestGitHubReleaseApi).not.toHaveBeenCalled();
    expect(button('重新读取').disabled).toBe(true);
  });
  it('shows a content conflict without a success message or publication retry', async () => {
    api.syncLatestGitHubReleaseApi.mockRejectedValue({ status: 409, serverCode: 'release_conflict' }); await mount();
    expect(host.textContent).toContain('同一版本在 Fuwu 中存在不同内容，自动发布已停止');
    expect(messages.success).not.toHaveBeenCalled(); expect(api.publishReleaseApi).not.toHaveBeenCalled();
    expect(api.stageRemoteReleaseApi).not.toHaveBeenCalled();
  });
  it('reconciles a timed out sync with the next already-published result', async () => {
    api.syncLatestGitHubReleaseApi.mockRejectedValueOnce({ status: 504 }); await mount();
    expect(host.textContent).toContain('下次检查时确认发布状态');
    api.getReleaseListApi.mockResolvedValue([{ project_id: 'heyue', version: '1.0.14', channel: 'stable', latest: true }]);
    button('立即检查并同步').click(); await flush();
    expect(host.textContent).toContain('Fuwu 已包含 GitHub 最新正式版 v1.0.14');
    expect(host.querySelector('pre')?.textContent).toContain('1.0.14'); expect(api.getReleaseListApi).toHaveBeenCalledTimes(2);
  });
  it('checks every five minutes and stops checking after leaving the page', async () => {
    await mount(); await vi.advanceTimersByTimeAsync(5 * 60 * 1000);
    expect(api.syncLatestGitHubReleaseApi).toHaveBeenCalledTimes(2);
    app?.unmount(); app = undefined; await vi.advanceTimersByTimeAsync(5 * 60 * 1000);
    expect(api.syncLatestGitHubReleaseApi).toHaveBeenCalledTimes(2); expect(vi.getTimerCount()).toBe(0);
  });
  it.each(['project', 'channel'])('discards an old sync response after changing %s and checks the new context', async (context) => {
    const pending = deferred<{ status: string; latest: ReturnType<typeof latest> }>();
    api.syncLatestGitHubReleaseApi.mockReturnValueOnce(pending.promise); await mount();
    if (context === 'project') { route.meta.projectId = 'bark'; route.meta.projectName = 'Bark'; }
    else { const select = host.querySelector('select')!; select.value = 'beta'; select.dispatchEvent(new Event('change')); }
    await flush(); expect(api.syncLatestGitHubReleaseApi).toHaveBeenCalledTimes(1);
    pending.resolve({ status: 'published', latest: latest('heyue', '9.9.9') }); await flush();
    expect(messages.success).not.toHaveBeenCalled(); expect(host.textContent).not.toContain('9.9.9');
    await vi.advanceTimersByTimeAsync(0); await flush();
    expect(api.syncLatestGitHubReleaseApi).toHaveBeenLastCalledWith(context === 'project' ? 'bark' : 'heyue', context === 'channel' ? 'beta' : 'stable');
    expect(api.syncLatestGitHubReleaseApi).toHaveBeenCalledTimes(2);
  });
  it('coalesces timer checks while a request is in flight', async () => {
    const pending = deferred<{ status: string; latest: ReturnType<typeof latest> }>();
    api.syncLatestGitHubReleaseApi.mockReturnValueOnce(pending.promise); await mount();
    await vi.advanceTimersByTimeAsync(10 * 60 * 1000); expect(api.syncLatestGitHubReleaseApi).toHaveBeenCalledTimes(1);
    pending.resolve({ status: 'already_published', latest: latest() }); await flush(); await vi.advanceTimersByTimeAsync(0);
    expect(api.syncLatestGitHubReleaseApi).toHaveBeenCalledTimes(2);
  });
  it('does not announce publication or reload data when a sync completes after unmount', async () => {
    const pending = deferred<{ status: string; latest: ReturnType<typeof latest> }>();
    api.syncLatestGitHubReleaseApi.mockReturnValueOnce(pending.promise); await mount(); app?.unmount(); app = undefined;
    pending.resolve({ status: 'published', latest: latest() }); await flush();
    expect(messages.success).not.toHaveBeenCalled(); expect(api.getReleaseListApi).toHaveBeenCalledTimes(1); expect(vi.getTimerCount()).toBe(0);
  });
});
