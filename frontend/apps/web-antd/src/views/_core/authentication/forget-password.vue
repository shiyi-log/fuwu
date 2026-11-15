<script lang="ts" setup>
import { onUnmounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';

import { Button, Form, Input, message } from 'ant-design-vue';

import {
  requestPasswordResetCodeApi,
  resetPasswordWithCodeApi,
} from '#/api/core/auth';

defineOptions({ name: 'ForgetPassword' });

const router = useRouter();
const form = reactive({ email: '', code: '', newPassword: '', confirmPassword: '' });
const sendingCode = ref(false);
const resetting = ref(false);
const cooldown = ref(0);
let pageDisposed = false;
let cooldownTimer: ReturnType<typeof setInterval> | undefined;

function validEmail(value: string) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value.trim());
}

async function sendCode() {
  if (pageDisposed || cooldown.value > 0 || sendingCode.value) return;
  const email = form.email.trim().toLowerCase();
  if (!validEmail(email)) {
    message.warning('请输入有效的邮箱地址');
    return;
  }
  sendingCode.value = true;
  try {
    await requestPasswordResetCodeApi({ email });
    if (pageDisposed) return;
    message.success('如果该邮箱对应有效账号，验证码将发送至该邮箱');
    cooldown.value = 60;
    cooldownTimer = setInterval(() => {
      cooldown.value -= 1;
      if (cooldown.value <= 0 && cooldownTimer) {
        clearInterval(cooldownTimer);
        cooldownTimer = undefined;
      }
    }, 1000);
  } catch {
    if (!pageDisposed) message.error('验证码发送失败，请稍后重试');
  } finally {
    if (!pageDisposed) sendingCode.value = false;
  }
}

async function submit() {
  if (pageDisposed || resetting.value) return;
  const email = form.email.trim().toLowerCase();
  if (!validEmail(email)) {
    message.warning('请输入有效的邮箱地址');
    return;
  }
  if (!/^\d{6}$/.test(form.code.trim())) {
    message.warning('请输入 6 位邮箱验证码');
    return;
  }
  if (form.newPassword.length < 12 || form.newPassword.length > 256 || !form.newPassword.trim()) {
    message.warning('新密码需为 12–256 个字符');
    return;
  }
  if (form.newPassword !== form.confirmPassword) {
    message.warning('两次输入的新密码不一致');
    return;
  }

  resetting.value = true;
  try {
    await resetPasswordWithCodeApi({
      email,
      code: form.code.trim(),
      newPassword: form.newPassword,
    });
    if (pageDisposed) return;
    form.newPassword = '';
    form.confirmPassword = '';
    form.code = '';
    message.success('密码已重置，请使用新密码登录');
    await router.push('/auth/login');
  } catch {
    if (!pageDisposed) message.error('密码重置失败，请检查验证码或稍后重试');
  } finally {
    if (!pageDisposed) resetting.value = false;
  }
}

onUnmounted(() => {
  pageDisposed = true;
  if (cooldownTimer) clearInterval(cooldownTimer);
});
</script>

<template>
  <div class="mx-auto w-full max-w-md">
    <div class="mb-7">
      <h1 class="mb-2 text-2xl font-semibold">重置密码</h1>
      <p class="text-muted-foreground">通过账号邮箱接收验证码并设置新密码。</p>
    </div>

    <Form layout="vertical" @finish="submit">
      <Form.Item label="邮箱地址" required>
        <Input
          v-model:value="form.email"
          autocomplete="email"
          placeholder="name@example.com"
          type="email"
          :maxlength="254"
          @press-enter="sendCode"
        />
      </Form.Item>
      <Form.Item label="邮箱验证码" required>
        <div class="flex gap-2">
          <Input
            v-model:value="form.code"
            autocomplete="one-time-code"
            inputmode="numeric"
            placeholder="输入 6 位验证码"
            :maxlength="6"
            @press-enter="submit"
          />
          <Button
            class="shrink-0"
            :disabled="cooldown > 0 || sendingCode"
            :loading="sendingCode"
            @click="sendCode"
          >
            {{ cooldown > 0 ? `${cooldown} 秒后重发` : '获取验证码' }}
          </Button>
        </div>
      </Form.Item>
      <Form.Item label="新密码" required>
        <Input.Password
          v-model:value="form.newPassword"
          autocomplete="new-password"
          placeholder="12–256 个字符"
          :maxlength="256"
        />
      </Form.Item>
      <Form.Item label="确认新密码" required>
        <Input.Password
          v-model:value="form.confirmPassword"
          autocomplete="new-password"
          placeholder="再次输入新密码"
          :maxlength="256"
          @press-enter="submit"
        />
      </Form.Item>
      <Button block html-type="submit" type="primary" :loading="resetting">
        确认重置密码
      </Button>
      <Button block class="mt-3" @click="router.push('/auth/login')">
        返回登录
      </Button>
    </Form>
  </div>
</template>
