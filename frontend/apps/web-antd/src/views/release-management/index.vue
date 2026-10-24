<script setup lang="ts">
import type {
  ReleaseAuditRecord,
  ReleaseChannel,
  ReleaseRecord,
  ReleaseStorageStatus,
} from '#/api/release';

import { computed, reactive, ref, watch } from 'vue';
import { useRoute } from 'vue-router';

import { useAccess } from '@vben/access';
import { Page } from '@vben/common-ui';

import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  message,
  Modal,
  Progress,
  Select,
  Space,
  Spin,
  Table,
  Tag,
} from 'ant-design-vue';

import {
  getLatestGitHubReleaseApi,
  getReleaseAuditApi,
  getReleaseListApi,
  getReleaseStorageStatusApi,
  publishReleaseApi,
  rollbackReleaseApi,
  stageLatestReleaseApi,
  stageRemoteReleaseApi,
} from '#/api';

import { isReleaseConflictError } from '#/api/request-errors';
import { isStageOutcomeUncertain } from './stage-errors';

const { hasAccessByCodes } = useAccess();
const route = useRoute();
const versionPattern = /^[0-9]\.[0-9]\.[0-9]$/;
const stagingRefPattern = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;

const loading = ref(false);
const submitting = ref(false);
const errorMessage = ref('');
const releases = ref<ReleaseRecord[]>([]);
const audits = ref<ReleaseAuditRecord[]>([]);
const storage = ref<ReleaseStorageStatus>({});
const selectedProject = computed(() => String(route.meta.projectId || ''));
const selectedChannel = ref<ReleaseChannel>('stable');
const publishOpen = ref(false);
const remoteOpen = ref(false);
const stageRemotePending = ref(false);
const latestReleasePending = ref(false);
const stageContextRevision = ref(0);
const rollbackOpen = ref(false);
const selectedRelease = ref<ReleaseRecord>();

const publishModel = reactive({
  project: '',
  channel: 'stable',
  version: '',
  tag: '',
  revision: '',
  staging_ref: '',
});
const rollbackModel = reactive({
  project: '',
  channel: 'stable',
  version: '',
  confirmation: '',
  reason: '',
});
const remoteModel = reactive({ tag: '', staging_ref: '', assets: '', manifest: '' });

const canPublish = computed(
  () => hasAccessByCodes(['release.storage.publish']),
);
const canRollback = computed(
  () => hasAccessByCodes(['release.storage.rollback']),
);
const jobLabels = { validation: '校验', publish: '发布', rollback: '回滚' };
const jobs = computed(() =>
  Object.entries(jobLabels).map(([operation, title]) => ({
    operation,
    title,
    job: storage.value.jobs?.[operation as keyof typeof jobLabels],
  })),
);

const projectLabel = (project?: string) => {
  return project === selectedProject.value ? String(route.meta.projectName || project) : project || '—';
};
const releaseProject = (release: ReleaseRecord) =>
  release.project || release.project_id;
const releaseDigest = (release: ReleaseRecord) => release.digest || '—';
const formatDate = (value?: null | string) => {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? value
    : date.toLocaleString('zh-CN', { hour12: false });
};
const reachabilityLabel = computed(() => {
  if (storage.value.reachable === true) return '可访问';
  if (storage.value.reachable === false) return '不可用';
  return '未检查';
});
const reachabilityColor = computed(() => {
  if (storage.value.reachable === true) return 'green';
  if (storage.value.reachable === false) return 'red';
  return 'default';
});
const connectivityCheckedAt = computed(() =>
  storage.value.connectivityCheckedAt ?? storage.value.checked_at,
);
const formatBytes = (value?: number) => {
  if (value === undefined || value === null) return '—';
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
};
const asRelease = (value: unknown) => value as ReleaseRecord;
const auditField = (value: unknown, key: unknown) => {
  const record = value as Record<string, unknown>;
  const field = typeof key === 'string' ? key : '';
  const result = record[field];
  if (result === undefined || result === null || result === '') return '—';
  return typeof result === 'object' ? JSON.stringify(result) : String(result);
};
const statusColor = (value?: string) => {
  const status = String(value || '').toLowerCase();
  if (['completed', 'healthy', 'published', 'ready', 'rolled_back', 'succeeded', 'success', 'validated', 'verified'].includes(status)) return 'green';
  if (['error', 'failed', 'rejected', 'unreachable', 'unverified'].includes(status)) return 'red';
  return 'orange';
};
const progressPercent = (value?: number) =>
  typeof value === 'number' && Number.isFinite(value)
    ? Math.max(0, Math.min(100, value))
    : 0;

const filteredReleases = computed(() =>
  releases.value.filter((item) => {
    const projectMatches = !selectedProject.value || releaseProject(item) === selectedProject.value;
    const channelMatches = item.channel === selectedChannel.value;
    return projectMatches && channelMatches;
  }),
);
const channelOptions = ['stable', 'beta'];

const releaseColumns = [
  { title: '项目', dataIndex: 'project', key: 'project' },
  { title: '频道', dataIndex: 'channel', key: 'channel' },
  { title: '版本', dataIndex: 'version', key: 'version' },
  { title: 'Revision', dataIndex: 'revision', key: 'revision' },
  { title: 'Digest', dataIndex: 'digest', key: 'digest' },
  { title: '大小', dataIndex: 'size', key: 'size' },
  { title: '对象 HEAD', dataIndex: 'head_status', key: 'head_status' },
  { title: '发布时间', dataIndex: 'published_at', key: 'published_at' },
  { title: '操作', key: 'actions' },
];
const auditColumns = [
  { title: '时间', dataIndex: 'created_at', key: 'created_at' },
  { title: 'Actor', dataIndex: 'actor', key: 'actor' },
  { title: '操作', dataIndex: 'action', key: 'action' },
  { title: '项目 / 频道', dataIndex: 'project', key: 'project' },
  { title: '版本', dataIndex: 'version', key: 'version' },
  { title: '状态', dataIndex: 'status', key: 'status' },
  { title: '任务进度', dataIndex: 'progress', key: 'progress' },
  { title: 'Before', dataIndex: 'before', key: 'before' },
  { title: 'After', dataIndex: 'after', key: 'after' },
];

let loadRevision = 0;
async function loadData() {
  const revision = ++loadRevision;
  const project = selectedProject.value;
  const channel = selectedChannel.value;
  if (!project) return;
  loading.value = true;
  errorMessage.value = '';
  try {
    const [releaseData, storageData, auditData] = await Promise.all([
      getReleaseListApi(project, channel),
      getReleaseStorageStatusApi(project, channel),
      getReleaseAuditApi(project, channel),
    ]);
    if (revision !== loadRevision) return;
    releases.value = releaseData;
    storage.value = storageData || {};
    audits.value = auditData;
  } catch {
    if (revision !== loadRevision) return;
    errorMessage.value = '发行数据加载失败，请确认后端服务和访问权限已配置。';
  } finally {
    if (revision === loadRevision) loading.value = false;
  }
}

function showPublishModal() {
  publishModel.project = selectedProject.value;
  publishModel.channel = selectedChannel.value;
  publishOpen.value = true;
}
function showRemoteModal() {
  if (stageRemotePending.value) {
    publishOpen.value = true;
    message.warning('当前 staging 结果仍待确认，请先继续提交已有 staging 引用，不要重复下载。');
    return;
  }
  remoteModel.tag = '';
  remoteModel.staging_ref = '';
  remoteModel.assets = '';
  remoteModel.manifest = '';
  remoteOpen.value = true;
}

function generatedStagingRef(project: string, tag: string) {
  const normalizedProject = project.replaceAll(/[^A-Za-z0-9._-]/g, '-');
  const normalizedTag = tag.replaceAll(/[^A-Za-z0-9._-]/g, '-');
  return `job-${normalizedProject}-${normalizedTag}-${Date.now().toString(36)}`.slice(0, 128);
}

async function discoverLatestRelease() {
  if (stageRemotePending.value) {
    publishOpen.value = true;
    message.warning('当前 staging 结果仍待确认，请先继续提交已有 staging 引用，不要重复下载。');
    return;
  }
  const project = selectedProject.value;
  const channel = selectedChannel.value;
  const contextRevision = stageContextRevision.value;
  if (!project) {
    message.warning('当前没有选择项目。');
    return;
  }
  latestReleasePending.value = true;
  try {
    const latest = await getLatestGitHubReleaseApi(project);
    if (contextRevision !== stageContextRevision.value || selectedProject.value !== project || selectedChannel.value !== channel) return;
    remoteModel.tag = latest.tag;
    remoteModel.staging_ref = generatedStagingRef(project, latest.tag);
    remoteModel.assets = latest.assets.join(', ');
    remoteModel.manifest = JSON.stringify(latest.manifest, null, 2);
    remoteOpen.value = true;
    message.success(`已发现最新正式 Release ${latest.tag}，请确认后下载到 staging。`);
  } catch {
    message.error('自动获取最新正式 Release 失败，请确认 GitHub 授权和项目仓库配置。');
  } finally {
    latestReleasePending.value = false;
  }
}

async function stageLatestRelease() {
  if (stageRemotePending.value) {
    publishOpen.value = true;
    message.warning('当前 staging 结果仍待确认，请先继续提交已有 staging 引用，不要重复下载。');
    return;
  }
  const project = selectedProject.value;
  const channel = selectedChannel.value;
  const contextRevision = stageContextRevision.value;
  if (!project) {
    message.warning('当前没有选择项目。');
    return;
  }
  const stagingRef = generatedStagingRef(project, 'latest');
  latestReleasePending.value = true;
  let latest: Awaited<ReturnType<typeof getLatestGitHubReleaseApi>> | undefined;
  try {
    // Resolve metadata first so a browser timeout can still resume publishing
    // the exact staging job without starting a duplicate download.
    latest = await getLatestGitHubReleaseApi(project);
    if (contextRevision !== stageContextRevision.value || selectedProject.value !== project || selectedChannel.value !== channel) return;
    const staged = await stageLatestReleaseApi({ project, staging_ref: stagingRef, latest: true });
    if (contextRevision !== stageContextRevision.value || selectedProject.value !== project || selectedChannel.value !== channel) {
      message.warning('项目或频道已切换，已丢弃本次 staging 结果，请在当前项目重新发起操作。');
      return;
    }
    stageRemotePending.value = false;
    const stagedRelease = staged && typeof staged === 'object' && 'release' in staged
      ? (staged as { release?: Record<string, unknown> }).release
      : undefined;
    const tag = typeof stagedRelease?.tag === 'string' ? stagedRelease.tag : latest.tag;
    const version = typeof stagedRelease?.version === 'string' ? stagedRelease.version : latest.version;
    const revision = typeof stagedRelease?.revision === 'string' ? stagedRelease.revision : latest.revision;
    preparePublishFromManifest(
      { ...latest.manifest, tag, version, revision },
      tag,
      stagingRef,
      project,
      channel,
    );
    publishOpen.value = true;
    message.success(`已自动发现并下载最新正式 Release ${latest.tag} 到 staging，请继续提交发布。`);
  } catch (error) {
    if (contextRevision !== stageContextRevision.value || selectedProject.value !== project || selectedChannel.value !== channel) return;
    if (isStageOutcomeUncertain(error)) {
      stageRemotePending.value = true;
      if (latest) {
        // Metadata was fetched before staging; preserving it lets the operator
        // continue with publish when the server finished after a browser timeout.
        preparePublishFromManifest(latest.manifest, latest.tag, stagingRef, project, channel);
      }
      publishOpen.value = true;
      message.warning('最新 Release 的 staging 结果暂时无法确认；请稍后直接提交当前 staging 引用，不要重复下载。');
      return;
    }
    message.error('自动下载最新正式 Release 失败，请确认 GitHub 授权、Release 资产和校验清单。');
  } finally {
    latestReleasePending.value = false;
  }
}
function resetStagingState() {
  remoteOpen.value = false;
  stageRemotePending.value = false;
  Object.assign(remoteModel, { tag: '', staging_ref: '', assets: '', manifest: '' });
  Object.assign(publishModel, { project: '', channel: 'stable', version: '', tag: '', revision: '', staging_ref: '' });
}
function preparePublishFromManifest(
  manifest: Record<string, unknown>,
  tag: string,
  stagingRef: string,
  project: string,
  channel: ReleaseChannel,
) {
  const version = typeof manifest.version === 'string' ? manifest.version : tag.slice(1);
  const revision = typeof manifest.revision === 'string' ? manifest.revision : '';
  Object.assign(publishModel, {
    project,
    channel,
    version,
    tag,
    revision,
    staging_ref: stagingRef,
  });
}
async function submitRemote() {
  if (stageRemotePending.value) {
    publishOpen.value = true;
    message.warning('当前 staging 结果仍待确认，请先继续提交已有 staging 引用，不要重复下载。');
    return;
  }
  const operationProject = selectedProject.value;
  const operationChannel = selectedChannel.value;
  const operationRevision = stageContextRevision.value;
  const operationTag = remoteModel.tag;
  const operationStagingRef = remoteModel.staging_ref;
  const assets = remoteModel.assets.split(',').map((item) => item.trim()).filter(Boolean);
  if (!operationTag || !operationStagingRef || assets.length === 0 || !remoteModel.manifest.trim()) {
    message.warning('请填写 Release tag、staging 引用、资产名称和 manifest JSON。');
    return;
  }
  if (!/^v[0-9]\.[0-9]\.[0-9]$/.test(operationTag) || !stagingRefPattern.test(operationStagingRef)) {
    message.warning('Tag 或 staging 引用格式不正确。');
    return;
  }
  let manifest: Record<string, unknown>;
  try {
    const parsed = JSON.parse(remoteModel.manifest);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('invalid');
    manifest = parsed as Record<string, unknown>;
  } catch {
    message.warning('manifest 必须是 JSON 对象。');
    return;
  }
  submitting.value = true;
  try {
    await stageRemoteReleaseApi({ project: operationProject, tag: operationTag, staging_ref: operationStagingRef, assets, manifest });
    if (operationRevision !== stageContextRevision.value || selectedProject.value !== operationProject || selectedChannel.value !== operationChannel) {
      message.warning('项目或频道已切换，已丢弃本次 staging 结果，请在当前项目重新发起操作。');
      return;
    }
    message.success('远端 Release 已下载到受保护 staging；请继续提交发布任务。');
    remoteOpen.value = false;
    stageRemotePending.value = false;
    preparePublishFromManifest(manifest, operationTag, operationStagingRef, operationProject, operationChannel);
    publishOpen.value = true;
  } catch (error) {
    if (operationRevision !== stageContextRevision.value || selectedProject.value !== operationProject || selectedChannel.value !== operationChannel) {
      return;
    }
    if (isStageOutcomeUncertain(error)) {
      // The server may still be finishing the synchronous download after the
      // browser gives up. Preserve the exact staging_ref and manifest-derived
      // publish fields so the operator can continue with POST /publish and
      // does not create a second staging job that will conflict.
      stageRemotePending.value = true;
      remoteOpen.value = false;
      preparePublishFromManifest(manifest, operationTag, operationStagingRef, operationProject, operationChannel);
      publishOpen.value = true;
      message.warning('远端 staging 结果暂时无法确认；已保留 staging 引用，请稍后直接提交发布，不要重复下载。');
      return;
    }
    message.error('远端 Release 下载失败，请确认 GitHub 授权、资产和校验清单。');
  } finally {
    submitting.value = false;
  }
}
function showRollbackModal(record: ReleaseRecord) {
  selectedRelease.value = record;
  rollbackModel.project = releaseProject(record) || '';
  rollbackModel.channel = record.channel;
  rollbackModel.version = record.version;
  rollbackModel.confirmation = '';
  rollbackModel.reason = '';
  rollbackOpen.value = true;
}
async function submitPublish() {
  if (!publishModel.project || !publishModel.version || !publishModel.revision || !publishModel.staging_ref) {
    message.warning('请填写项目、数字版本、40 位 revision 和受保护 staging 引用。');
    return;
  }
  if (!versionPattern.test(publishModel.version)) {
    message.warning('版本须为三段单个数字，例如 0.0.9 后使用 0.1.0，不接受 0.0.10。');
    return;
  }
  if (!/^[0-9a-f]{40}$/i.test(publishModel.revision)) {
    message.warning('Revision 须为完整 40 位十六进制提交 SHA。');
    return;
  }
  if (!stagingRefPattern.test(publishModel.staging_ref)) {
    message.warning('staging 引用须为合法 job 标识，仅支持字母、数字、点、下划线和短横线。');
    return;
  }
  if (publishModel.tag && publishModel.tag !== `v${publishModel.version}`) {
    message.warning('Tag 须与数字版本一致，例如版本 0.0.1 对应 v0.0.1。');
    return;
  }
  submitting.value = true;
  try {
    await publishReleaseApi({
      project: publishModel.project,
      channel: publishModel.channel,
      version: publishModel.version,
      tag: publishModel.tag || `v${publishModel.version}`,
      revision: publishModel.revision,
      staging_ref: publishModel.staging_ref,
    });
    message.success('发布任务已提交，服务端校验通过后才会更新 latest 指针。');
    stageRemotePending.value = false;
    publishOpen.value = false;
    await loadData();
  } catch (error) {
    // The request interceptor already presents the bounded conflict message;
    // avoid showing the same toast again from this submit handler.
    if (!isReleaseConflictError(error)) {
      message.error('发布任务提交失败，请查看服务端返回的安全错误信息。');
    }
  } finally {
    submitting.value = false;
  }
}
async function submitRollback() {
  if (rollbackModel.confirmation !== 'ROLLBACK' || !rollbackModel.reason) {
    message.warning('请输入 ROLLBACK，并填写回滚原因。');
    return;
  }
  submitting.value = true;
  try {
    await rollbackReleaseApi({
      project: rollbackModel.project,
      channel: rollbackModel.channel,
      version: rollbackModel.version,
      confirm: true,
      reason: rollbackModel.reason,
    });
    message.success('回滚任务已提交，只会切换 latest 指针。');
    rollbackOpen.value = false;
    await loadData();
  } catch {
    message.error('回滚任务提交失败，请确认超级管理员权限和二次确认。');
  } finally {
    submitting.value = false;
  }
}

watch([selectedProject, selectedChannel], () => {
  stageContextRevision.value += 1;
  publishOpen.value = false;
  rollbackOpen.value = false;
  resetStagingState();
  releases.value = [];
  audits.value = [];
  storage.value = {};
  void loadData();
}, { immediate: true });
</script>

<template>
  <Page description="查看当前项目 stable / beta 版本、对象校验与发行审计" :title="`${projectLabel(selectedProject)} · 发行版本`">
    <Alert
      v-if="errorMessage"
      :message="errorMessage"
      class="mb-4"
      closable
      show-icon
      type="error"
      @close="errorMessage = ''"
    />

    <div class="mb-4 flex flex-wrap items-center justify-between gap-3">
      <Space wrap>
        <Select v-model:value="selectedChannel" style="width: 130px">
          <Select.Option v-for="channel in channelOptions" :key="channel" :value="channel">{{ channel }}</Select.Option>
        </Select>
        <Button :loading="loading" @click="loadData">刷新</Button>
      </Space>
      <Space v-if="canPublish"><Button :loading="latestReleasePending" @click="stageLatestRelease">自动下载并 staging 最新正式 Release</Button><Button @click="showRemoteModal">按 Tag 获取 Release</Button><Button type="primary" @click="showPublishModal">发布版本</Button></Space>
    </div>

    <Spin :spinning="loading">
      <div class="grid grid-cols-1 gap-4 xl:grid-cols-4">
        <Card title="对象存储状态"><Tag :color="reachabilityColor">{{ reachabilityLabel }}</Tag><div class="mt-3 text-sm text-gray-500">{{ storage.provider || '—' }} / {{ storage.prefix || 'releases' }}</div><div class="mt-1 text-xs text-gray-400">{{ storage.bucket || 'Bucket 未返回' }} · {{ storage.region || '区域未返回' }}</div><div class="mt-1 text-xs text-gray-400">连通性检查：{{ formatDate(connectivityCheckedAt) }}</div></Card>
        <Card title="当前项目"><div class="text-xl font-semibold">{{ projectLabel(selectedProject) }}</div><div class="text-xs text-gray-400">{{ selectedProject }} / {{ selectedChannel }}</div></Card>
        <Card title="版本数"><div class="text-3xl font-semibold">{{ storage.releases ?? releases.length }}</div><div class="text-xs text-gray-400">immutable 记录</div></Card>
        <Card title="审计记录"><div class="text-3xl font-semibold">{{ audits.length }}</div><div class="text-xs text-gray-400">发布、校验与回滚</div></Card>
      </div>

      <div class="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Card v-for="item in jobs" :key="item.operation" :title="`${item.title}任务`">
          <Tag :color="statusColor(item.job?.status)">{{ item.job?.status || '尚无任务' }}</Tag>
          <Progress v-if="item.job" class="mt-3" :percent="progressPercent(item.job.progress)" :status="item.job.status === 'failed' ? 'exception' : 'normal'" />
          <div class="mt-2 break-all font-mono text-xs text-gray-500">{{ item.job?.requestId || '—' }}</div>
          <div class="mt-1 text-xs text-gray-400">{{ formatDate(item.job?.updatedAt) }}</div>
        </Card>
      </div>

      <Card class="mt-4" title="版本清单">
        <Table :columns="releaseColumns" :data-source="filteredReleases" :pagination="{ pageSize: 10 }" row-key="id" :scroll="{ x: 1100 }" size="small">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'project'">{{ projectLabel(releaseProject(asRelease(record))) }}</template>
            <template v-else-if="column.key === 'channel'"><Tag :color="record.channel === 'stable' ? 'blue' : 'gold'">{{ record.channel }}</Tag></template>
            <template v-else-if="column.key === 'version'"><span class="font-semibold">{{ record.version }}</span><Tag v-if="record.latest" color="green" class="ml-1">latest</Tag></template>
            <template v-else-if="column.key === 'revision'"><span :title="record.revision" class="font-mono text-xs">{{ record.revision?.slice(0, 12) || '—' }}</span></template>
            <template v-else-if="column.key === 'digest'"><span :title="releaseDigest(asRelease(record))" class="font-mono text-xs">{{ releaseDigest(asRelease(record)).slice(0, 16) }}</span></template>
            <template v-else-if="column.key === 'size'"><div v-for="artifact in asRelease(record).artifacts" :key="artifact.key" class="text-xs">{{ artifact.name }}：{{ formatBytes(artifact.size) }}</div><span v-if="!asRelease(record).artifacts?.length">{{ formatBytes(record.size) }}</span></template>
            <template v-else-if="column.key === 'head_status'"><Tag :color="statusColor(record.head_status)">{{ record.head_status || '未返回' }}</Tag></template>
            <template v-else-if="column.key === 'published_at'">{{ formatDate(record.published_at) }}</template>
            <template v-else-if="column.key === 'actions'"><Button v-if="canRollback" danger size="small" @click="showRollbackModal(asRelease(record))">回滚到此版本</Button></template>
          </template>
        </Table>
      </Card>

      <Card class="mt-4" title="发布 / 校验 / 回滚审计">
        <Table :columns="auditColumns" :data-source="audits" :pagination="{ pageSize: 10 }" row-key="id" :scroll="{ x: 1100 }" size="small">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'created_at'">{{ formatDate(record.created_at) }}</template>
            <template v-else-if="column.key === 'project'">{{ projectLabel(record.project || record.project_id) }} / {{ record.channel || '—' }}</template>
            <template v-else-if="column.key === 'action'">{{ record.action || record.operation || '—' }}</template>
            <template v-else-if="column.key === 'status'"><Tag :color="statusColor(record.status)">{{ record.status || '—' }}</Tag></template>
            <template v-else-if="column.key === 'progress'"><Progress v-if="typeof record.progress === 'number'" :percent="progressPercent(record.progress)" size="small" :status="record.status === 'failed' ? 'exception' : 'normal'" /><span v-else>—</span></template>
            <template v-else-if="column.key === 'before' || column.key === 'after'"><span class="font-mono text-xs">{{ auditField(record, column.key) }}</span></template>
            <template v-else>{{ auditField(record, column.dataIndex) }}</template>
          </template>
        </Table>
      </Card>
    </Spin>

    <Modal v-model:open="publishOpen" :confirm-loading="submitting" ok-text="提交发布任务" title="提交发行发布" @ok="submitPublish">
      <Alert class="mb-4" message="只允许提交受保护 staging 引用；控制台不会接收任意本地路径或云凭据。" show-icon type="info" />
      <Alert v-if="stageRemotePending" class="mb-4" message="远端 staging 结果暂时无法确认" description="服务端可能仍在下载和校验资产。请稍后直接提交当前 staging 引用；不要再次发起远端下载，否则会产生 staging 冲突。" show-icon type="warning" />
      <Form layout="vertical">
        <Form.Item label="当前项目" required><Input :value="publishModel.project" disabled /></Form.Item>
        <Form.Item label="频道"><Select v-model:value="publishModel.channel"><Select.Option value="stable">stable</Select.Option><Select.Option value="beta">beta</Select.Option></Select></Form.Item>
        <Form.Item label="数字版本" required><Input v-model:value="publishModel.version" pattern="[0-9]\.[0-9]\.[0-9]" placeholder="例如 0.0.1，每段单个数字，9 向左进位" /></Form.Item>
        <Form.Item label="Tag"><Input v-model:value="publishModel.tag" placeholder="可选，默认 v + 数字版本" /></Form.Item>
        <Form.Item label="完整 40 位 Revision" required><Input v-model:value="publishModel.revision" :maxlength="40" placeholder="40 位 commit SHA" /></Form.Item>
        <Form.Item label="受保护 staging 引用" required><Input v-model:value="publishModel.staging_ref" :maxlength="128" placeholder="例如 job-20261020-001" /></Form.Item>
      </Form>
    </Modal>

    <Modal v-model:open="remoteOpen" :confirm-loading="submitting" ok-text="下载远端 Release" title="从 GitHub Release 获取" @ok="submitRemote">
      <Alert class="mb-4" message="服务端会按项目绑定的 GitHub 仓库读取 Release，并校验远端 SHA256SUMS；下载成功后仍需提交发布任务。" show-icon type="info" />
      <Button class="mb-4" :loading="latestReleasePending" @click="discoverLatestRelease">自动填充最新正式 Release</Button>
      <Form layout="vertical">
        <Form.Item label="Release Tag" required><Input v-model:value="remoteModel.tag" placeholder="例如 v1.0.2" /></Form.Item>
        <Form.Item label="受保护 staging 引用" required><Input v-model:value="remoteModel.staging_ref" :maxlength="128" placeholder="例如 job-20261021-001" /></Form.Item>
        <Form.Item label="资产名称（逗号分隔）" required><Input v-model:value="remoteModel.assets" placeholder="例如 app.tar.gz, app.zip" /></Form.Item>
        <Form.Item label="manifest JSON" required><Input.TextArea v-model:value="remoteModel.manifest" :rows="10" placeholder="粘贴包含 assets、revision、releaseId 等字段的 manifest.json" /></Form.Item>
      </Form>
    </Modal>

    <Modal v-model:open="rollbackOpen" :confirm-loading="submitting" ok-text="确认回滚" title="确认回滚 latest 指针" @ok="submitRollback">
      <Alert class="mb-4" message="回滚只切换 latest 指针，不删除 immutable 历史资产。需要超级管理员权限。" show-icon type="warning" />
      <p>目标版本：<strong>{{ selectedRelease?.version }}</strong>（{{ selectedRelease?.channel }}）</p>
      <Form layout="vertical">
        <Form.Item label="二次确认" required><Input v-model:value="rollbackModel.confirmation" placeholder="输入 ROLLBACK" /></Form.Item>
        <Form.Item label="回滚原因" required><Input.TextArea v-model:value="rollbackModel.reason" :rows="3" placeholder="说明回滚原因" /></Form.Item>
      </Form>
    </Modal>
  </Page>
</template>
