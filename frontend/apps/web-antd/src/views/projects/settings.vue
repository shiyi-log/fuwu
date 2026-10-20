<script setup lang="ts">
import type { GithubAuthorization, ProjectRecord } from '#/api/platform';
import { computed, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useAccess } from '@vben/access';
import { Page } from '@vben/common-ui';
import { Alert, Button, Card, Descriptions, Form, Input, message, Spin, Space, Switch, Tag } from 'ant-design-vue';
import { authorizeGithubApi, getGithubAuthorizationApi, listProjectsApi, revokeGithubAuthorizationApi, updateProjectApi } from '#/api';
import { refreshProjectNavigation } from '#/router/access';

const route = useRoute(); const router = useRouter();
const { hasAccessByCodes } = useAccess();
const id = computed(() => String(route.meta.projectId || ''));
const canManage = computed(() => hasAccessByCodes(['projects.manage']));
const project = ref<ProjectRecord>(); const loading = ref(false); const saving = ref(false); const error = ref('');
const github = ref<GithubAuthorization>(); const githubToken = ref(''); const githubSaving = ref(false);
const model = reactive({ name: '', description: '', public_api: '', build_instructions: '', active: true });
let loadRevision = 0;
async function load() {
  const revision = ++loadRevision;
  const selected = id.value; if (!selected) return;
  project.value = undefined;
  loading.value = true; error.value = '';
  try {
    const item = (await listProjectsApi()).find((entry) => entry.id === selected);
    if (revision !== loadRevision) return;
    if (!item) { error.value = '项目不存在或当前账号没有访问权限。'; return; }
    project.value = item; Object.assign(model, { name: item.name, description: item.description || '', public_api: item.public_api || '', build_instructions: item.build_instructions || '', active: item.active });
    github.value = canManage.value ? await getGithubAuthorizationApi(selected) : undefined;
  } catch { if (revision === loadRevision) error.value = '项目设置加载失败。'; }
  finally { if (revision === loadRevision) loading.value = false; }
}
async function saveGithub() {
  if (!githubToken.value.trim()) { message.warning('请输入 GitHub 访问令牌'); return; }
  githubSaving.value = true;
  try { github.value = await authorizeGithubApi(id.value, githubToken.value.trim()); githubToken.value = ''; message.success('GitHub 账号已授权，令牌仅保存在服务端受保护文件中'); }
  catch { /* Shared client displays safe server errors. */ }
  finally { githubSaving.value = false; }
}
async function revokeGithub() {
  githubSaving.value = true;
  try { github.value = await revokeGithubAuthorizationApi(id.value); message.success('GitHub 授权已撤销'); }
  catch { /* Shared client displays safe server errors. */ }
  finally { githubSaving.value = false; }
}
async function save() {
  if (!model.name.trim()) { message.warning('请输入项目名称'); return; }
  saving.value = true;
  try { await updateProjectApi(id.value, { name: model.name.trim(), description: model.description.trim(), public_api: model.public_api.trim(), build_instructions: model.build_instructions.trim(), active: model.active }); message.success('项目设置已保存'); await refreshProjectNavigation(router); await load(); }
  catch { /* Shared client handles safe errors. */ }
  finally { saving.value = false; }
}
watch(id, load, { immediate: true });
</script>
<template>
  <Page :title="`${project?.name || id} · 项目设置`" description="项目元数据、公开 API 说明与构建方式由有权限的管理员维护">
    <Alert v-if="error" :message="error" show-icon type="error" class="mb-4" />
    <Spin :spinning="loading"><template v-if="project"><Card><Descriptions :column="1" bordered class="mb-6"><Descriptions.Item label="项目标识">{{ project.id }}</Descriptions.Item><Descriptions.Item label="代码仓库">{{ project.repository }}</Descriptions.Item><Descriptions.Item label="创建时间">{{ project.created_at || '—' }}</Descriptions.Item></Descriptions><Form layout="vertical" style="max-width: 800px"><Form.Item label="项目名称" required><Input v-model:value="model.name" :disabled="!canManage" :maxlength="120" /></Form.Item><Form.Item label="项目说明"><Input.TextArea v-model:value="model.description" :disabled="!canManage" :rows="4" :maxlength="1000" /></Form.Item><Form.Item label="公开 API 说明" extra="填写面向使用者公开的接口定义、认证方式和请求响应格式。"><Input.TextArea v-model:value="model.public_api" :disabled="!canManage" :rows="8" :maxlength="12000" placeholder="接口用途、公开地址、认证方式、请求与响应格式" /></Form.Item><Form.Item label="构建方式" extra="填写运行环境、依赖安装与构建命令。"><Input.TextArea v-model:value="model.build_instructions" :disabled="!canManage" :rows="8" :maxlength="12000" placeholder="例如：安装运行时、安装依赖、执行构建命令" /></Form.Item><Form.Item label="启用项目"><Switch v-model:checked="model.active" :disabled="!canManage" /></Form.Item><Button v-if="canManage" type="primary" :loading="saving" @click="save">保存项目设置</Button></Form></Card><Card v-if="canManage" title="GitHub 账号授权" class="mt-4"><div class="mb-3 text-sm text-gray-500">授权后，远端发行下载可以读取该项目 GitHub 仓库的私有 Release。令牌只会发送到服务端并保存在受保护文件中。</div><div class="mb-3"><Tag :color="github?.authorized ? 'green' : 'default'">{{ github?.authorized ? `已授权：${github.login}` : '未授权' }}</Tag><span v-if="github?.authorizedAt" class="ml-2 text-xs text-gray-400">{{ github.authorizedAt }}</span></div><Form layout="vertical"><Form.Item label="GitHub Personal Access Token"><Input.Password v-model:value="githubToken" :maxlength="512" autocomplete="new-password" placeholder="仅用于校验和远端下载，不会回显" /></Form.Item><Space><Button type="primary" :loading="githubSaving" @click="saveGithub">授权账号</Button><Button v-if="github?.authorized" danger :loading="githubSaving" @click="revokeGithub">撤销授权</Button></Space></Form></Card></template></Spin>
  </Page>
</template>
