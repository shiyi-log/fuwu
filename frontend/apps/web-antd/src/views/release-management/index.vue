<script setup lang="ts">
import type {
  LatestGitHubRelease,
  ReleaseAuditRecord,
  ReleaseChannel,
  ReleaseRecord,
  ReleaseStorageStatus,
} from '#/api/release';

import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue';
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
  stageRemoteReleaseApi,
  syncLatestGitHubReleaseApi,
} from '#/api';

import { isReleaseConflictError } from '#/api/request-errors';
import { isStageOutcomeUncertain } from './stage-errors';

const { hasAccessByCodes } = useAccess();
const route = useRoute();
const versionPattern = /^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$/;
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
const latestSyncPending = ref(false);
const latestRelease = ref<LatestGitHubRelease>();
const latestSyncState = ref<'already_published' | 'current_newer' | 'error' | 'loading' | 'published' | 'read_only' | 'idle'>('idle');
const latestSyncError = ref('');
const latestCheckedAt = ref('');
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
const canReadLatest = computed(
  () => hasAccessByCodes(['release.storage.read']),
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
const currentChannelLatest = computed(() =>
  filteredReleases.value.find((item) => item.latest),
);
function compareVersions(left: string, right: string) {
  const leftParts = left.split('.');
  const rightParts = right.split('.');
  for (let index = 0; index < Math.max(leftParts.length, rightParts.length); index += 1) {
    const leftPart = leftParts[index] || '0';
    const rightPart = rightParts[index] || '0';
    if (leftPart.length !== rightPart.length) return leftPart.length - rightPart.length;
    if (leftPart !== rightPart) return leftPart > rightPart ? 1 : -1;
  }
  return 0;
}
const latestSyncMessage = computed(() => {
  if (latestSyncState.value === 'loading') return '正在检查 GitHub 正式版…';
  if (latestSyncState.value === 'error') return latestSyncError.value;
  if (!latestRelease.value) return '尚未读取上游版本';
  if (latestSyncState.value === 'published') {
    return `已自动同步 ${latestRelease.value.tag} 到 ${selectedChannel.value}`;
  }
  if (latestSyncState.value === 'current_newer') {
    return `Fuwu ${latestSyncMessageCurrentVersion.value} 高于 GitHub ${latestRelease.value.version}，已跳过降级`;
  }
  if (latestSyncState.value === 'already_published') {
    return `Fuwu 已包含 GitHub 最新正式版 ${latestRelease.value.tag}`;
  }
  if (latestSyncState.value === 'read_only') {
    const current = currentChannelLatest.value;
    if (!current) return `GitHub 最新正式版 ${latestRelease.value.tag} 尚未发布到 ${selectedChannel.value}`;
    const comparison = compareVersions(latestRelease.value.version, current.version);
    if (comparison > 0) return `发现上游新版本 ${latestRelease.value.tag}，当前 ${selectedChannel.value} 为 ${current.version}`;
    if (comparison < 0) return `GitHub ${latestRelease.value.version} 低于当前 ${selectedChannel.value} ${current.version}`;
    return latestRelease.value.revision === current.revision
      ? `GitHub ${latestRelease.value.tag} 与当前 ${selectedChannel.value} 版本一致`
      : `GitHub ${latestRelease.value.tag} 与当前版本号相同，但 Revision 不同`;
  }
  return '自动检查会在进入页面及定期刷新时运行';
});
const latestSyncMessageCurrentVersion = computed(() => currentChannelLatest.value?.version || '当前发行');
const latestSyncColor = computed(() => {
  if (latestSyncState.value === 'error') return 'red';
  if (latestSyncState.value === 'published' || latestSyncState.value === 'already_published') return 'green';
  if (latestSyncState.value === 'current_newer') return 'orange';
  if (latestSyncState.value === 'read_only' && latestRelease.value) {
    const current = currentChannelLatest.value;
    return current && compareVersions(latestRelease.value.version, current.version) <= 0 ? 'green' : 'orange';
  }
  return 'blue';
});
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

function latestRequestError(error: unknown) {
  const safeError = error as { code?: string; serverCode?: string; status?: number };
  const messages: Record<string, string> = {
    github_authorization_required: '未配置 GitHub 授权，无法读取私有仓库 Release。',
    github_repository_access_denied: 'GitHub 授权账号无权读取项目绑定的仓库。',
    github_token_invalid: 'GitHub 授权已失效，请在系统设置中重新授权。',
    github_unavailable: 'GitHub 暂时不可用，稍后会自动重试。',
    remote_asset_missing: '最新 Release 缺少 manifest 声明的资产。',
    remote_asset_unavailable: '最新 Release 资产下载失败，稍后会自动重试。',
    remote_checksum_mismatch: 'Release 资产校验失败，自动发布已停止。',
    remote_checksums_invalid: 'Release 的 SHA256SUMS 格式无效，自动发布已停止。',
    remote_checksums_missing: '最新 Release 缺少 SHA256SUMS。',
    remote_manifest_missing: '最新 Release 缺少 manifest.json。',
    remote_manifest_unavailable: '最新 Release 的 manifest 暂时无法读取。',
    remote_release_not_found: '绑定仓库尚无正式 Release。',
    remote_release_unavailable: 'GitHub Release 信息暂时无法读取，稍后会自动重试。',
    remote_size_mismatch: 'Release 资产大小校验失败，自动发布已停止。',
    release_conflict: '同一版本在 Fuwu 中存在不同内容，自动发布已停止。',
    immutable_object_conflict: '对象存储中已存在不同内容的同版本资产，自动发布已停止。',
  };
  if (safeError.serverCode && messages[safeError.serverCode]) {
    return messages[safeError.serverCode] || '自动读取或发布失败；稍后会自动重试。';
  }
  if (safeError.code === 'ECONNABORTED' || safeError.code === 'ETIMEDOUT' || safeError.status === 504) {
    return '自动同步超时；页面会在下次检查时确认发布状态。';
  }
  if (safeError.status === 403) return '当前账号没有读取或发布此项目发行的权限。';
  if (safeError.status === 502) return 'GitHub 或对象存储暂时不可用，稍后会自动重试。';
  return '自动读取或发布失败；请检查项目仓库、GitHub 授权和对象存储状态。';
}

let latestReadRevision = 0;
let latestRefreshQueued = false;
let latestPageDisposed = false;
let latestCheckTimer: ReturnType<typeof setInterval> | undefined;
async function refreshLatestRelease() {
  const project = selectedProject.value;
  if (latestPageDisposed || !project || !canReadLatest.value) return;
  if (latestSyncPending.value) {
    latestRefreshQueued = true;
    return;
  }
  const channel = selectedChannel.value;
  const contextRevision = stageContextRevision.value;
  const revision = ++latestReadRevision;
  latestSyncPending.value = true;
  latestSyncState.value = 'loading';
  latestSyncError.value = '';
  try {
    if (canPublish.value) {
      const result = await syncLatestGitHubReleaseApi(project, channel);
      if (revision !== latestReadRevision || contextRevision !== stageContextRevision.value || selectedProject.value !== project || selectedChannel.value !== channel) return;
      latestRelease.value = result.latest;
      latestSyncState.value = result.status;
      if (result.status === 'published') {
        message.success(`已自动发布 GitHub ${result.latest.tag} 到 ${channel}`);
      }
      // A previous request may have completed after a browser timeout. Reload
      // even when the server now reports that the release is already present.
      await loadData();
    } else {
      const latest = await getLatestGitHubReleaseApi(project);
      if (revision !== latestReadRevision || contextRevision !== stageContextRevision.value || selectedProject.value !== project || selectedChannel.value !== channel) return;
      latestRelease.value = latest;
      latestSyncState.value = 'read_only';
    }
  } catch (error) {
    if (revision !== latestReadRevision || contextRevision !== stageContextRevision.value || selectedProject.value !== project || selectedChannel.value !== channel) return;
    latestSyncState.value = 'error';
    latestSyncError.value = latestRequestError(error);
  } finally {
    latestSyncPending.value = false;
    if (revision === latestReadRevision) latestCheckedAt.value = new Date().toISOString();
    if (latestRefreshQueued && !latestPageDisposed) {
      latestRefreshQueued = false;
      setTimeout(() => void refreshLatestRelease(), 0);
    }
  }
}
function refreshPage() {
  void loadData();
  void refreshLatestRelease();
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
  if (!/^v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$/.test(operationTag) || !stagingRefPattern.test(operationStagingRef)) {
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
    message.warning('版本须为无前导零的三段数字，例如 1.0.14。');
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
  latestReadRevision += 1;
  publishOpen.value = false;
  rollbackOpen.value = false;
  resetStagingState();
  releases.value = [];
  audits.value = [];
  storage.value = {};
  latestRelease.value = undefined;
  latestCheckedAt.value = '';
  latestSyncError.value = '';
  latestSyncState.value = canReadLatest.value ? 'loading' : 'idle';
  void loadData();
  void refreshLatestRelease();
}, { immediate: true });
watch(canReadLatest, (allowed) => {
  if (allowed) void refreshLatestRelease();
});

onMounted(() => {
  latestCheckTimer = setInterval(() => void refreshLatestRelease(), 5 * 60 * 1000);
});
onUnmounted(() => {
  latestPageDisposed = true;
  latestRefreshQueued = false;
  latestReadRevision += 1;
  if (latestCheckTimer) clearInterval(latestCheckTimer);
});
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
        <Button :loading="loading || latestSyncPending" @click="refreshPage">刷新</Button>
      </Space>
      <Space v-if="canPublish"><Button @click="showRemoteModal">按 Tag 获取 Release</Button><Button type="primary" @click="showPublishModal">发布版本</Button></Space>
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

      <Card class="mt-4" title="GitHub 最新正式版">
        <div class="flex flex-wrap items-center justify-between gap-4">
          <div class="min-w-0">
            <div class="flex flex-wrap items-center gap-2">
              <Tag :color="latestSyncColor">{{ latestSyncState === 'loading' ? '检查中' : latestSyncState === 'error' ? '检查失败' : latestSyncState === 'published' ? '已自动发布' : latestSyncState === 'already_published' ? '已同步' : latestSyncState === 'current_newer' ? '跳过降级' : latestSyncState === 'read_only' ? '已读取' : '未检查' }}</Tag>
              <span class="font-semibold">{{ latestRelease?.tag || '—' }}</span>
              <span v-if="latestRelease" class="text-sm text-gray-500">版本 {{ latestRelease.version }}</span>
            </div>
            <div class="mt-2 break-all text-sm text-gray-500">{{ latestSyncMessage }}</div>
            <div v-if="latestRelease" class="mt-1 break-all text-xs text-gray-400">{{ latestRelease.repository }} · {{ latestRelease.revision.slice(0, 12) }} · GitHub 发布时间 {{ formatDate(latestRelease.publishedAt) }}</div>
            <div class="mt-1 text-xs text-gray-400">最近检查：{{ formatDate(latestCheckedAt) }}</div>
          </div>
          <Button :loading="latestSyncPending" :disabled="!canReadLatest" @click="refreshLatestRelease">{{ canPublish ? '立即检查并同步' : '重新读取' }}</Button>
        </div>
      </Card>

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
        <Form.Item label="数字版本" required><Input v-model:value="publishModel.version" pattern="(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)" placeholder="例如 1.0.14" /></Form.Item>
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
