<script setup lang="ts">
import type { OperationLog } from '#/api/platform';
import type { ReleaseStorageStatus } from '#/api/release';
import { computed, ref, watch } from 'vue';
import { useRoute } from 'vue-router';
import { useAccess } from '@vben/access';
import { Page } from '@vben/common-ui';
import { Alert, Button, Card, Progress, Select, Space, Table, Tag } from 'ant-design-vue';
import { getReleaseStorageStatusApi, listLogsApi } from '#/api';

const route = useRoute(); const { hasAccessByCodes } = useAccess();
const project = computed(() => String(route.meta.projectId || ''));
const channel = ref('stable'); const category = ref<string>(); const level = ref<string>();
const loading = ref(false); const error = ref(''); const records = ref<OperationLog[]>([]); const storage = ref<ReleaseStorageStatus>({});
const canReadRelease = computed(() => hasAccessByCodes(['release.storage.read']));
const jobs = computed(() => ([['validation', '校验'], ['publish', '发布'], ['rollback', '回滚']] as const).map(([key, title]) => ({ key, title, value: storage.value.jobs?.[key] })));
const columns = [
  { title: '时间', dataIndex: 'created_at', key: 'created_at', width: 190 },
  { title: '级别', key: 'level', width: 90 },
  { title: '类别', dataIndex: 'category', key: 'category' },
  { title: '操作', dataIndex: 'action', key: 'action' },
  { title: '执行人', dataIndex: 'actor', key: 'actor' },
  { title: '请求标识', dataIndex: 'request_id', key: 'request_id' },
  { title: '方法', dataIndex: 'method', key: 'method' },
  { title: '路由', dataIndex: 'route', key: 'route' },
  { title: 'HTTP', dataIndex: 'status_code', key: 'status_code' },
  { title: '耗时(ms)', dataIndex: 'duration_ms', key: 'duration_ms' },
];
let loadRevision = 0;
async function load() {
  const revision = ++loadRevision;
  if (!project.value) return;
  records.value = []; storage.value = {};
  loading.value = true; error.value = '';
  try {
    const [logs, status] = await Promise.all([listLogsApi({ project: project.value, channel: channel.value, category: category.value, level: level.value, limit: 50 }), canReadRelease.value ? getReleaseStorageStatusApi(project.value, channel.value) : Promise.resolve({})]);
    if (revision === loadRevision) { records.value = logs; storage.value = status; }
  } catch { if (revision === loadRevision) error.value = '任务与日志加载失败，请检查访问权限或重试。'; }
  finally { if (revision === loadRevision) loading.value = false; }
}
watch([project, channel, category, level], load, { immediate: true });
</script>
<template>
  <Page :title="`${String(route.meta.projectName || project)} · 任务与日志`" description="显示当前项目的真实发布任务和最近 50 条运营日志">
    <Alert v-if="error" :message="error" type="error" show-icon class="mb-4" />
    <Space wrap class="mb-4"><Select v-model:value="channel" style="width: 130px" :options="[{ value: 'stable', label: 'stable' }, { value: 'beta', label: 'beta' }]" /><Select v-model:value="category" allow-clear placeholder="全部类别" style="width: 150px" :options="[{ value: 'auth', label: '认证' }, { value: 'account', label: '账号' }, { value: 'project', label: '项目' }, { value: 'release', label: '发行' }, { value: 'settings', label: '设置' }]" /><Select v-model:value="level" allow-clear placeholder="全部级别" style="width: 130px" :options="[{ value: 'info', label: '信息' }, { value: 'warning', label: '警告' }, { value: 'error', label: '错误' }]" /><Button :loading="loading" @click="load">刷新</Button></Space>
    <div v-if="canReadRelease" class="mb-4 grid grid-cols-1 gap-4 xl:grid-cols-3"><Card v-for="job in jobs" :key="job.key" :title="`${job.title}任务`"><Tag>{{ job.value?.status || '尚无任务' }}</Tag><Progress v-if="job.value" :percent="job.value.progress" :status="job.value.status === 'failed' ? 'exception' : 'normal'" class="mt-3" /><div class="mt-2 break-all font-mono text-xs">{{ job.value?.requestId || '—' }}</div><div class="mt-1 text-xs text-gray-500">{{ job.value?.updatedAt || '—' }}</div></Card></div>
    <Card title="运营日志"><Table :columns="columns" :data-source="records" :loading="loading" row-key="id" :scroll="{ x: 1400 }" :pagination="{ pageSize: 20 }" size="small"><template #bodyCell="{ column, record }"><Tag v-if="column.key === 'level'" :color="record.level === 'error' ? 'red' : record.level === 'warning' ? 'orange' : 'blue'">{{ record.level }}</Tag></template></Table></Card>
  </Page>
</template>
