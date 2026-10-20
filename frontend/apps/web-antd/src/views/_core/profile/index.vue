<script setup lang="ts">
import type { AccountRecord } from '#/api/platform';
import { computed, reactive, ref } from 'vue';
import { Page } from '@vben/common-ui';
import { useUserStore } from '@vben/stores';
import { Alert, Button, Card, Descriptions, Form, Input, message, Tag } from 'ant-design-vue';
import { changePasswordApi } from '#/api';
import { useAuthStore } from '#/store';

const userStore = useUserStore(); const authStore = useAuthStore();
const user = computed(() => userStore.userInfo as unknown as AccountRecord | null);
const saving = ref(false);
const model = reactive({ currentPassword: '', newPassword: '', confirmation: '' });
async function save() {
  if (!model.currentPassword || model.newPassword.length < 12 || model.newPassword.length > 256 || !model.newPassword.trim()) { message.warning('请输入当前密码，新密码需为 12–256 个字符'); return; }
  if (model.newPassword !== model.confirmation) { message.warning('两次输入的新密码不一致'); return; }
  saving.value = true;
  try { await changePasswordApi(model.currentPassword, model.newPassword); message.success('密码已修改，请重新登录'); await authStore.logout(false); }
  catch { /* Passwords never enter console output or client diagnostics. */ }
  finally { Object.assign(model, { currentPassword: '', newPassword: '', confirmation: '' }); saving.value = false; }
}
</script>
<template>
  <Page title="个人资料" description="查看当前账号的实际资料、授权范围并修改密码">
    <div class="grid grid-cols-1 gap-4 xl:grid-cols-2"><Card title="账号资料"><Descriptions :column="1" bordered><Descriptions.Item label="用户名">{{ user?.username || '—' }}</Descriptions.Item><Descriptions.Item label="显示名称">{{ user?.displayName || '—' }}</Descriptions.Item><Descriptions.Item label="角色"><Tag v-for="role in user?.roles || []" :key="role">{{ role }}</Tag></Descriptions.Item><Descriptions.Item label="权限"><div class="break-all text-xs">{{ user?.permissions?.join('、') || '无额外操作权限' }}</div></Descriptions.Item><Descriptions.Item label="项目范围">{{ user?.project_ids === null ? '全部项目' : user?.project_ids?.join('、') || '无项目授权' }}</Descriptions.Item></Descriptions></Card><Card title="修改密码"><Alert message="保存后撤销当前账号全部会话，需要使用新密码重新登录。" show-icon type="info" class="mb-4" /><Form layout="vertical"><Form.Item label="当前密码" required><Input.Password v-model:value="model.currentPassword" autocomplete="current-password" /></Form.Item><Form.Item label="新密码" required><Input.Password v-model:value="model.newPassword" autocomplete="new-password" :maxlength="256" placeholder="12–256 个字符" /></Form.Item><Form.Item label="确认新密码" required><Input.Password v-model:value="model.confirmation" autocomplete="new-password" :maxlength="256" /></Form.Item><Button type="primary" :loading="saving" @click="save">保存新密码</Button></Form></Card></div>
  </Page>
</template>
