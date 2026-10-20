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
        placeholder: '输入用户名',
      },
      fieldName: 'username',
      label: '用户名',
      rules: z.string().trim().min(1, { message: '请输入用户名' }),
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
    :show-forget-password="false"
    :show-qrcode-login="false"
    :show-register="false"
    :show-remember-me="false"
    :show-third-party-login="false"
    title="登录 fuwu"
    sub-title="使用管理员分配的账号登录，按授权项目管理发行。"
    submit-button-text="登录"
    @submit="authStore.authLogin"
  />
</template>
