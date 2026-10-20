<script setup lang="ts">
import type { ProjectRecord } from '#/api/platform';

import { computed, onMounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';
import { useAccess } from '@vben/access';
import { Page } from '@vben/common-ui';
import { useUserStore } from '@vben/stores';
import { Alert, Button, Card, Form, Input, message, Modal, Space, Table, Tag } from 'ant-design-vue';

import { createProjectApi, listProjectsApi } from '#/api';
import { refreshProjectNavigation } from '#/router/access';

const router = useRouter();
const userStore = useUserStore();
const { hasAccessByCodes } = useAccess();
const projects = ref<ProjectRecord[]>([]);
const loading = ref(false);
const saving = ref(false);
const open = ref(false);
const error = ref('');
const model = reactive({ id: '', name: '', repository: '', description: '' });
const canRegister = computed(() => hasAccessByCodes(['projects.manage']) && userStore.userInfo?.roles?.some((role) => ['super', 'superadmin'].includes(role)));
const columns = [
  { title: '项目', dataIndex: 'name', key: 'name' },
  { title: '标识', dataIndex: 'id', key: 'id' },
  { title: '代码仓库', dataIndex: 'repository', key: 'repository' },
  { title: '说明', dataIndex: 'description', key: 'description' },
  { title: '状态', key: 'active' },
  { title: '操作', key: 'actions' },
];
async function load() {
  loading.value = true; error.value = '';
  try { projects.value = await listProjectsApi(); }
  catch { error.value = '项目加载失败，请刷新或联系管理员检查项目授权。'; }
  finally { loading.value = false; }
}
async function save() {
  if (!/^[a-z0-9][a-z0-9._-]{0,62}$/.test(model.id) || !model.name.trim() || !model.repository.trim()) {
    message.warning('请填写合法项目标识、项目名和代码仓库。项目标识只支持小写字母、数字、点、短横线与下划线。'); return;
  }
  saving.value = true;
  try {
    await createProjectApi({ id: model.id, name: model.name.trim(), repository: model.repository.trim(), description: model.description.trim() });
    open.value = false; message.success('项目已注册');
    await refreshProjectNavigation(router); await load();
    Object.assign(model, { id: '', name: '', repository: '', description: '' });
  } catch { /* The shared request client displays the server's safe error. */ }
  finally { saving.value = false; }
}
const navigate = (id: string, page: string) => router.push(`/projects/${encodeURIComponent(id)}/${page}`);
onMounted(load);
</script>

<template>
  <Page title="项目列表" description="按项目进入发行版本、任务日志与项目设置">
    <Alert v-if="error" :message="error" show-icon type="error" class="mb-4" />
    <Card>
      <div class="mb-4 flex items-center justify-between"><span class="text-gray-500">当前账号可访问 {{ projects.length }} 个项目</span><Space><Button :loading="loading" @click="load">刷新</Button><Button v-if="canRegister" type="primary" @click="open = true">注册项目</Button></Space></div>
      <Table :columns="columns" :data-source="projects" :loading="loading" row-key="id" :pagination="{ pageSize: 20 }" :scroll="{ x: 850 }">
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'active'"><Tag :color="record.active ? 'green' : 'default'">{{ record.active ? '启用' : '停用' }}</Tag></template>
          <template v-else-if="column.key === 'actions'"><Space wrap><Button type="link" @click="navigate(record.id, 'documentation')">项目说明</Button><Button v-if="hasAccessByCodes(['release.storage.read'])" type="link" @click="navigate(record.id, 'releases')">发行版本</Button><Button v-if="hasAccessByCodes(['logs.read'])" type="link" @click="navigate(record.id, 'logs')">任务与日志</Button><Button type="link" @click="navigate(record.id, 'settings')">项目设置</Button></Space></template>
        </template>
      </Table>
    </Card>
    <Modal v-model:open="open" title="注册项目" :confirm-loading="saving" ok-text="创建项目" @ok="save">
      <Form layout="vertical"><Form.Item label="项目标识" required><Input v-model:value="model.id" :maxlength="63" placeholder="例如 sample-app" /></Form.Item><Form.Item label="项目名称" required><Input v-model:value="model.name" :maxlength="120" /></Form.Item><Form.Item label="代码仓库" required><Input v-model:value="model.repository" placeholder="例如 example-org/sample-app" /></Form.Item><Form.Item label="项目说明"><Input.TextArea v-model:value="model.description" :rows="3" :maxlength="1000" /></Form.Item></Form>
    </Modal>
  </Page>
</template>
