import type { App } from 'vue';

import { createApp, nextTick } from 'vue';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  requestCode: vi.fn(), resetPassword: vi.fn(), push: vi.fn(),
  success: vi.fn(), warning: vi.fn(), error: vi.fn(),
}));
vi.mock('vue-router', () => ({ useRouter: () => ({ push: mocks.push }) }));
vi.mock('#/api/core/auth', () => ({
  requestPasswordResetCodeApi: mocks.requestCode,
  resetPasswordWithCodeApi: mocks.resetPassword,
}));
vi.mock('ant-design-vue', async () => {
  const { defineComponent, h } = await import('vue');
  const Form = Object.assign(defineComponent({
    emits: ['finish'],
    setup(_, { emit, slots }) {
      return () => h('form', {
        onSubmit: (event: Event) => { event.preventDefault(); emit('finish'); },
      }, slots.default?.());
    },
  }), { Item: defineComponent({ setup: (_, { slots }) => () => h('div', slots.default?.()) }) });
  const Input = Object.assign(defineComponent({
    props: { value: String },
    emits: ['update:value', 'pressEnter'],
    setup(props, { attrs, emit }) {
      return () => h('input', {
        ...attrs, value: props.value,
        onInput: (event: Event) => emit('update:value', (event.target as HTMLInputElement).value),
        onKeydown: (event: KeyboardEvent) => { if (event.key === 'Enter') emit('pressEnter'); },
      });
    },
  }), { Password: {} });
  Input.Password = Input;
  return {
    Form, Input,
    Button: defineComponent({
      inheritAttrs: false,
      props: { htmlType: String, loading: Boolean, disabled: Boolean },
      setup(props, { attrs, slots }) {
        return () => h('button', { ...attrs, type: props.htmlType || 'button', disabled: props.disabled || props.loading }, slots.default?.());
      },
    }),
    message: { success: mocks.success, warning: mocks.warning, error: mocks.error },
  };
});

import ForgetPassword from './forget-password.vue';

let app: App | undefined;
let root: HTMLDivElement;
const password = 'test-password-12345';
async function settle() {
  for (let index = 0; index < 6; index += 1) await Promise.resolve();
  await nextTick();
}
function input(placeholder: string) {
  const element = root.querySelector<HTMLInputElement>(`input[placeholder="${placeholder}"]`);
  if (!element) throw new Error(`Missing input: ${placeholder}`);
  return element;
}
function codeButton() {
  const element = [...root.querySelectorAll('button')].find((button) => /获取验证码|秒后重发/.test(button.textContent || ''));
  if (!element) throw new Error('Missing code button');
  return element;
}
async function fill(placeholder: string, value: string) {
  const element = input(placeholder);
  element.value = value;
  element.dispatchEvent(new Event('input', { bubbles: true }));
  await nextTick();
}
async function fillValidForm() {
  await fill('name@example.com', 'USER@Example.com');
  await fill('输入 6 位验证码', '123456');
  await fill('12–256 个字符', password);
  await fill('再次输入新密码', password);
}
function submit() {
  root.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
}
function deferred() {
  let resolve!: (value?: unknown) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function unmount() { app?.unmount(); app = undefined; }
beforeEach(() => {
  vi.useFakeTimers();
  vi.resetAllMocks();
  mocks.requestCode.mockResolvedValue({});
  mocks.resetPassword.mockResolvedValue({});
  mocks.push.mockResolvedValue(undefined);
  root = document.createElement('div');
  document.body.append(root);
  app = createApp(ForgetPassword);
  app.mount(root);
});
afterEach(() => {
  unmount(); root.remove(); vi.clearAllTimers(); vi.useRealTimers();
});

describe('password recovery page', () => {
  it('rejects an invalid email before requesting a code', async () => {
    await fill('name@example.com', 'invalid'); codeButton().click(); await settle();
    expect(mocks.requestCode).not.toHaveBeenCalled();
    expect(mocks.warning).toHaveBeenCalledWith('请输入有效的邮箱地址');
  });
  it('normalizes the email and enforces the resend countdown', async () => {
    await fill('name@example.com', 'USER@Example.com'); codeButton().click(); await settle();
    expect(mocks.requestCode).toHaveBeenCalledWith({ email: 'user@example.com' });
    expect(codeButton().disabled).toBe(true);
    expect(codeButton().textContent).toContain('60 秒后重发');
    input('name@example.com').dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
    expect(mocks.requestCode).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(59_000);
    expect(codeButton().textContent).toContain('1 秒后重发');
    await vi.advanceTimersByTimeAsync(1000);
    expect(codeButton().disabled).toBe(false);
    codeButton().click(); await settle();
    expect(mocks.requestCode).toHaveBeenCalledTimes(2);
  });
  it('does not submit duplicate code requests while delivery is pending', async () => {
    const pending = deferred(); mocks.requestCode.mockReturnValue(pending.promise);
    await fill('name@example.com', 'user@example.com');
    input('name@example.com').dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
    input('name@example.com').dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
    expect(mocks.requestCode).toHaveBeenCalledTimes(1);
    pending.resolve(); await settle();
  });
  it('allows retry after a delivery failure without starting a cooldown', async () => {
    mocks.requestCode.mockRejectedValueOnce(new Error('delivery unavailable'));
    await fill('name@example.com', 'user@example.com'); codeButton().click(); await settle();
    expect(mocks.error).toHaveBeenCalledWith('验证码发送失败，请稍后重试');
    expect(codeButton().disabled).toBe(false);
    expect(codeButton().textContent).toContain('获取验证码');
    codeButton().click(); await settle();
    expect(mocks.requestCode).toHaveBeenCalledTimes(2);
  });
  it.each([
    ['输入 6 位验证码', '12345', '请输入 6 位邮箱验证码'],
    ['12–256 个字符', 'short', '新密码需为 12–256 个字符'],
    ['再次输入新密码', 'another-password-123', '两次输入的新密码不一致'],
  ])('validates %s before submitting a reset', async (placeholder, value, warning) => {
    await fillValidForm(); await fill(placeholder, value); submit(); await settle();
    expect(mocks.resetPassword).not.toHaveBeenCalled();
    expect(mocks.warning).toHaveBeenCalledWith(warning);
  });
  it.each(['invalid_code', 'expired_code'])('keeps the user on the recovery page for %s', async (code) => {
    mocks.resetPassword.mockRejectedValueOnce({ code });
    await fillValidForm(); submit(); await settle();
    expect(mocks.push).not.toHaveBeenCalled();
    expect(mocks.error).toHaveBeenCalledWith('密码重置失败，请检查验证码或稍后重试');
    expect(input('输入 6 位验证码').value).toBe('123456');
    expect(root.querySelector<HTMLButtonElement>('button[type="submit"]')?.disabled).toBe(false);
  });
  it('clears sensitive inputs and returns to login after resetting', async () => {
    await fillValidForm(); submit(); await settle();
    expect(mocks.resetPassword).toHaveBeenCalledWith({ email: 'user@example.com', code: '123456', newPassword: password });
    expect(mocks.push).toHaveBeenCalledWith('/auth/login');
    expect(input('输入 6 位验证码').value).toBe('');
    expect(input('12–256 个字符').value).toBe('');
    expect(input('再次输入新密码').value).toBe('');
  });
  it('serializes form and Enter submissions while a reset is pending', async () => {
    const pending = deferred(); mocks.resetPassword.mockReturnValue(pending.promise);
    await fillValidForm(); submit();
    input('输入 6 位验证码').dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' })); submit();
    expect(mocks.resetPassword).toHaveBeenCalledTimes(1);
    pending.resolve(); await settle();
  });
  it('does not start a cooldown or show messages after leaving during delivery', async () => {
    const pending = deferred(); mocks.requestCode.mockReturnValue(pending.promise);
    await fill('name@example.com', 'user@example.com'); codeButton().click(); unmount();
    pending.resolve(); await settle();
    expect(vi.getTimerCount()).toBe(0);
    expect(mocks.success).not.toHaveBeenCalled();
  });
  it('clears a running countdown when leaving the page', async () => {
    await fill('name@example.com', 'user@example.com'); codeButton().click(); await settle();
    expect(vi.getTimerCount()).toBe(1); unmount(); expect(vi.getTimerCount()).toBe(0);
  });
  it('does not navigate or show success after leaving during a reset', async () => {
    const pending = deferred(); mocks.resetPassword.mockReturnValue(pending.promise);
    await fillValidForm(); submit(); unmount(); pending.resolve(); await settle();
    expect(mocks.push).not.toHaveBeenCalled();
    expect(mocks.success).not.toHaveBeenCalled();
  });
});
