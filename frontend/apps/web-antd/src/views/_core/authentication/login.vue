<script lang="ts" setup>
import type { VbenFormSchema } from '@vben/common-ui';

import { computed } from 'vue';

import { AuthenticationLogin, z } from '@vben/common-ui';

import { useAuthStore } from '#/store';

defineOptions({ name: 'Login' });

const authStore = useAuthStore();

const formSchema = computed((): VbenFormSchema[] => {
  return [
    {
      component: 'VbenInput',
      componentProps: {
        autocomplete: 'username',
        placeholder: '输入邮箱或原用户名',
        type: 'text',
      },
      fieldName: 'username',
      label: '邮箱或用户名',
      rules: z.string().trim().min(1, { message: '请输入邮箱或用户名' }),
    },
    {
      component: 'VbenInputPassword',
      componentProps: { autocomplete: 'current-password', placeholder: '输入密码' },
      fieldName: 'password', label: '密码',
      rules: z.string().min(1, { message: '请输入密码' }),
    },
  ];
});
</script>

<template>
  <AuthenticationLogin
    :form-schema="formSchema"
    :loading="authStore.loginLoading"
    :show-code-login="false"
    forget-password-path="/auth/forget-password"
    :show-forget-password="true"
    :show-qrcode-login="false"
    :show-register="false"
    :show-remember-me="false"
    :show-third-party-login="false"
    title="登录 fuwu"
    sub-title="建议使用邮箱登录；原用户名仍可继续使用。"
    submit-button-text="登录"
    @submit="authStore.authLogin"
  />
</template>
