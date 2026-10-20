<script setup lang="ts">
import type { AccountRecord, ProjectRecord, RoleRecord } from '#/api/platform';
import { computed, onMounted, reactive, ref, watch } from 'vue';
import { Page } from '@vben/common-ui';
import { useUserStore } from '@vben/stores';
import { Alert, Button, Card, Checkbox, Form, Input, message, Modal, Select, Space, Switch, Table, Tag } from 'ant-design-vue';
import { createAccountApi, listAccountsApi, listProjectsApi, listRolesApi, resetAccountPasswordApi, updateAccountApi } from '#/api';

const userStore = useUserStore();
const records = ref<AccountRecord[]>([]); const projects = ref<ProjectRecord[]>([]); const roles = ref<RoleRecord[]>([]);
const loading = ref(false); const saving = ref(false); const error = ref(''); const open = ref(false); const editing = ref<AccountRecord>();
const resetTarget = ref<AccountRecord>(); const resetOpen = ref(false); const resetPassword = ref('');
const model = reactive({ username: '', displayName: '', password: '', active: true, roles: ['viewer'], permissions: [] as string[], project_ids: [] as string[], allProjects: false });
const permissions = [
  { value: 'release.storage.read', label: '发行读取' }, { value: 'release.storage.publish', label: '发行发布' },
  { value: 'release.storage.rollback', label: '发行回滚' }, { value: 'projects.manage', label: '项目管理' },
  { value: 'users.manage', label: '账号管理' }, { value: 'logs.read', label: '运营日志读取' }, { value: 'settings.manage', label: '系统设置管理' },
];
const canGrantFullScope = computed(() => model.roles.includes('superadmin') && userStore.userInfo?.roles?.some((role) => ['super', 'superadmin'].includes(role)));
const inherited = computed(() => [...new Set(roles.value.filter((role) => model.roles.includes(role.id)).flatMap((role) => role.permissions))]);
const columns = [
  { title: '用户名', dataIndex: 'username', key: 'username' }, { title: '显示名称', dataIndex: 'displayName', key: 'displayName' },
  { title: '角色', key: 'roles' }, { title: '项目范围', key: 'projects' }, { title: '状态', key: 'active' }, { title: '操作', key: 'actions' },
];
const roleName = (id: string) => roles.value.find((role) => role.id === id)?.name || id;
const projectName = (id: string) => projects.value.find((project) => project.id === id)?.name || id;
async function load() {
  loading.value = true; error.value = '';
  try { const data = await Promise.all([listAccountsApi(), listProjectsApi(), listRolesApi()]); [records.value, projects.value, roles.value] = data; }
  catch { error.value = '账号信息加载失败，请检查账号管理权限。'; }
  finally { loading.value = false; }
}
function show(value?: unknown) {
  const record = value as AccountRecord | undefined;
  editing.value = record;
  Object.assign(model, { username: record?.username || '', displayName: record?.displayName || '', password: '', active: record?.active ?? true, roles: [...(record?.roles || ['viewer'])], permissions: [...(record?.directPermissions || [])], project_ids: [...(record?.project_ids || [])], allProjects: record?.project_ids === null });
  open.value = true;
}
async function save() {
  if (!model.username.trim() || !model.displayName.trim() || !model.roles.length) { message.warning('请填写用户名、显示名称并至少选择一个角色'); return; }
  if (!editing.value && (model.password.length < 12 || model.password.length > 256 || !model.password.trim())) { message.warning('初始密码需为 12–256 个字符'); return; }
  saving.value = true;
  try {
    const fields = { displayName: model.displayName.trim(), active: model.active, roles: [...model.roles], permissions: [...model.permissions], project_ids: model.allProjects && canGrantFullScope.value ? null : [...model.project_ids] };
    if (editing.value) await updateAccountApi(editing.value.id, fields);
    else await createAccountApi({ ...fields, username: model.username.trim(), password: model.password });
    model.password = ''; open.value = false;
    message.success('账号已保存；角色、权限或范围变更会撤销该账号的已有会话'); await load();
  } catch { /* Do not log request objects containing passwords. */ }
  finally { saving.value = false; }
}
function showReset(value: unknown) { resetTarget.value = value as AccountRecord; resetPassword.value = ''; resetOpen.value = true; }
async function reset() {
  if (!resetTarget.value) return;
  if (resetPassword.value.length < 12 || resetPassword.value.length > 256 || !resetPassword.value.trim()) { message.warning('新密码需为 12–256 个字符'); return; }
  saving.value = true;
  try { await resetAccountPasswordApi(resetTarget.value.id, resetPassword.value); resetOpen.value = false; message.success('密码已重置，已有会话已撤销'); }
  catch { /* Shared client displays safe server errors. */ }
  finally { resetPassword.value = ''; saving.value = false; }
}
watch(canGrantFullScope, (allowed) => { if (!allowed) model.allProjects = false; });
onMounted(load);
</script>

<template>
  <Page title="账号管理" description="管理真实账号、角色、独立权限与可访问项目">
    <Alert v-if="error" :message="error" show-icon type="error" class="mb-4" />
    <Card><div class="mb-4 flex justify-end"><Space><Button :loading="loading" @click="load">刷新</Button><Button type="primary" @click="show()">创建账号</Button></Space></div><Table :columns="columns" :data-source="records" :loading="loading" row-key="id" :scroll="{ x: 950 }" :pagination="{ pageSize: 20 }"><template #bodyCell="{ column, record }"><template v-if="column.key === 'roles'"><Tag v-for="role in record.roles" :key="role">{{ roleName(role) }}</Tag></template><template v-else-if="column.key === 'projects'"><Tag v-if="record.project_ids === null" color="blue">全部项目</Tag><span v-else-if="!record.project_ids.length">无项目授权</span><Tag v-for="id in record.project_ids || []" v-else :key="id">{{ projectName(id) }}</Tag></template><Tag v-else-if="column.key === 'active'" :color="record.active ? 'green' : 'default'">{{ record.active ? '启用' : '停用' }}</Tag><template v-else-if="column.key === 'actions'"><Space><Button type="link" @click="show(record)">编辑</Button><Button type="link" @click="showReset(record)">重置密码</Button></Space></template></template></Table></Card>
    <Modal v-model:open="open" :title="editing ? '编辑账号' : '创建账号'" :confirm-loading="saving" ok-text="保存账号" @ok="save" @after-close="model.password = ''">
      <Form layout="vertical"><Form.Item label="用户名" required><Input v-model:value="model.username" :disabled="!!editing" autocomplete="off" :maxlength="80" /></Form.Item><Form.Item label="显示名称" required><Input v-model:value="model.displayName" :maxlength="120" /></Form.Item><Form.Item v-if="!editing" label="初始密码" required><Input.Password v-model:value="model.password" autocomplete="new-password" :maxlength="256" placeholder="至少 12 个字符" /></Form.Item><Form.Item label="角色" required><Select v-model:value="model.roles" mode="multiple" :options="roles.map((role) => ({ value: role.id, label: role.name }))" /></Form.Item><div class="mb-4 text-xs text-gray-500">角色自带权限：{{ inherited.join('、') || '无' }}</div><Form.Item label="额外授权"><Select v-model:value="model.permissions" mode="multiple" :options="permissions" placeholder="只配置角色之外的额外权限" /></Form.Item><Form.Item label="项目范围"><Checkbox v-if="canGrantFullScope" v-model:checked="model.allProjects" class="mb-3">全部项目（仅超级管理员）</Checkbox><Select v-if="!model.allProjects" v-model:value="model.project_ids" mode="multiple" :options="projects.map((project) => ({ value: project.id, label: project.name }))" placeholder="未选择项目表示没有项目访问权" /></Form.Item><Form.Item label="启用账号"><Switch v-model:checked="model.active" /></Form.Item><Alert message="修改角色、权限、项目范围或启用状态会撤销该账号的所有现有会话。" type="info" show-icon /></Form>
    </Modal>
    <Modal v-model:open="resetOpen" title="重置密码" :confirm-loading="saving" ok-text="确认重置" @ok="reset" @after-close="resetPassword = ''"><Alert :message="`为 ${resetTarget?.username || ''} 设置新密码，保存后撤销其全部会话。`" show-icon type="warning" class="mb-4" /><Input.Password v-model:value="resetPassword" autocomplete="new-password" :maxlength="256" placeholder="12–256 个字符" /></Modal>
  </Page>
</template>
