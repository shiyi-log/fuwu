<script setup lang="ts">
import type {
  SystemUpdateAsset,
  SystemUpdateJob,
  SystemUpdateResponse,
} from '#/api/system-update';

import { computed, onBeforeUnmount, onMounted, ref } from 'vue';

import { useAccess } from '@vben/access';
import { Page } from '@vben/common-ui';

import {
  Alert,
  Button,
  Card,
  Descriptions,
  List,
  Modal,
  Progress,
  Space,
  Spin,
  Tag,
  message,
} from 'ant-design-vue';

import {
  getSystemUpdateApi,
  getSystemUpdateJobApi,
  startSystemUpdateApi,
} from '#/api';

const JOB_STORAGE_KEY = 'fuwu.system-update.job-id';
const TERMINAL_STATUSES = new Set<SystemUpdateJob['status']>([
  'succeeded',
  'failed',
  'rolled_back',
]);

const { hasAccessByCodes } = useAccess();
const canManage = computed(() => hasAccessByCodes(['fuwu.update.manage']));

const loading = ref(false);
const starting = ref(false);
const error = ref('');
const notice = ref('');
const jobQueryFailed = ref(false);
const update = ref<SystemUpdateResponse>();
const job = ref<SystemUpdateJob>();
const confirmOpen = ref(false);
let pollTimer: ReturnType<typeof setTimeout> | undefined;

function formatDate(value: string | null | undefined) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? value
    : date.toLocaleString('zh-CN', { hour12: false });
}

function formatBytes(value: number) {
  if (!Number.isFinite(value) || value < 0) return '—';
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 * 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`;
  return `${(value / 1024 / 1024 / 1024).toFixed(1)} GB`;
}

function safeReleaseUrl(value: string | undefined) {
  if (!value) return '';
  try {
    const url = new URL(value);
    if (url.protocol !== 'https:') return '';
    if (url.hostname !== 'github.com' || url.port || url.username || url.password || url.search || url.hash) return '';
    if (!/^\/shiyi-log\/fuwu\/releases\/tag\/v[0-9]+\.[0-9]+\.[0-9]+$/.test(url.pathname)) return '';
    return url.href;
  } catch {
    return '';
  }
}

function safeAssets(value: SystemUpdateAsset[] | undefined) {
  if (!Array.isArray(value)) return [];
  return value.filter((asset) =>
    asset && typeof asset.name === 'string' &&
    (typeof asset.digest === 'string' || asset.digest === null) &&
    Number.isFinite(asset.size) && asset.size >= 0,
  );
}

const releaseUrl = computed(() => safeReleaseUrl(update.value?.releaseUrl));
const assets = computed(() => safeAssets(update.value?.assets));
const statusLabel = computed(() => update.value?.updateAvailable ? '有可用更新' : '已是最新版本');
const statusColor = computed(() => update.value?.updateAvailable ? 'orange' : 'green');
const jobLabel = computed(() => ({
  queued: '排队中', downloading: '下载中', verifying: '校验中', preparing: '准备中',
  backing_up: '备份数据中', switching: '切换版本中', restarting: '重启服务中',
  health_check: '健康检查中', succeeded: '更新成功', failed: '更新失败',
  rolling_back: '正在回滚', rolled_back: '已回滚',
}[job.value?.status || 'queued'] || '处理中'));
const jobColor = computed(() => {
  if (job.value?.status === 'succeeded') return 'green';
  if (job.value?.status === 'failed' || job.value?.status === 'rolled_back') return 'red';
  return 'blue';
});
const updateBusy = computed(() => Boolean(job.value && !TERMINAL_STATUSES.has(job.value.status)));

function saveJobId(jobId: string) {
  try {
    if (typeof window !== 'undefined') {
      window.sessionStorage.setItem(JOB_STORAGE_KEY, jobId);
      return true;
    }
  } catch {
    return false;
  }
  return false;
}

function clearJobId() {
  try {
    if (typeof window !== 'undefined') window.sessionStorage.removeItem(JOB_STORAGE_KEY);
  } catch {
    // The current page can continue tracking the job without browser storage.
  }
}

function storedJobId() {
  try {
    if (typeof window === 'undefined') return '';
    return window.sessionStorage.getItem(JOB_STORAGE_KEY) || '';
  } catch {
    return '';
  }
}

function stopPolling() {
  if (pollTimer) clearTimeout(pollTimer);
  pollTimer = undefined;
}

async function pollJob(jobId: string) {
  stopPolling();
  jobQueryFailed.value = false;
  try {
    const current = await getSystemUpdateJobApi(jobId);
    job.value = current;
    if (TERMINAL_STATUSES.has(current.status)) {
      clearJobId();
      if (current.status === 'succeeded') {
        message.success(`系统已更新到 ${current.toVersion}`);
        await load(false);
      } else if (current.status === 'failed') {
        message.error(current.message || '系统更新失败');
      }
      return;
    }
    pollTimer = setTimeout(() => void pollJob(jobId), 2000);
  } catch {
    jobQueryFailed.value = true;
  }
}

function retryJobQuery() {
  const jobId = job.value?.jobId || storedJobId();
  if (jobId) void pollJob(jobId);
}

async function load(restoreJob = true) {
  if (!canManage.value) {
    update.value = undefined;
    error.value = '当前账号没有系统更新权限。';
    return;
  }
  loading.value = true;
  error.value = '';
  try {
    update.value = await getSystemUpdateApi();
    if (restoreJob && !job.value) {
      const jobId = storedJobId();
      if (jobId) {
        job.value = {
          jobId,
          status: 'queued',
          progress: 0,
          message: '正在读取更新任务状态…',
          errorCode: null,
          fromVersion: update.value.currentVersion,
          toVersion: update.value.latestVersion,
          startedAt: null,
          updatedAt: null,
        };
        await pollJob(jobId);
      }
    }
  } catch {
    update.value = undefined;
    error.value = '系统更新信息加载失败，请稍后重试。';
  } finally {
    loading.value = false;
  }
}

function showUpdateConfirm() {
  if (!update.value?.updateAvailable || updateBusy.value) return;
  confirmOpen.value = true;
}

async function startUpdate() {
  if (!update.value?.updateAvailable || updateBusy.value) return;
  starting.value = true;
  error.value = '';
  notice.value = '';
  try {
    const created = await startSystemUpdateApi(update.value.tag);
    job.value = created;
    if (!saveJobId(created.jobId)) {
      notice.value = '更新任务已提交，但浏览器无法保存任务 ID；请保持本页打开直至任务完成。';
    }
    confirmOpen.value = false;
    message.success('更新任务已提交，页面会持续显示执行进度。');
    await pollJob(created.jobId);
  } catch {
    error.value = '更新任务提交失败，请稍后重试。';
  } finally {
    starting.value = false;
  }
}

onMounted(() => void load());
onBeforeUnmount(stopPolling);
</script>

<template>
  <Page title="系统更新" description="检查并在线升级 Fuwu 服务">
    <Alert v-if="error" :message="error" show-icon type="error" class="mb-4" />
    <Alert v-if="notice" :message="notice" show-icon type="warning" class="mb-4" />
    <Spin :spinning="loading">
      <template v-if="update">
        <Card v-if="job" class="mb-4" title="更新任务">
          <div class="flex flex-wrap items-center gap-3">
            <Tag :color="jobColor">{{ jobLabel }}</Tag>
            <span class="text-sm text-gray-500">{{ job.message || '服务端正在处理更新任务。' }}</span>
          </div>
          <Progress class="mt-3" :percent="Math.max(0, Math.min(100, job.progress))" :status="job.status === 'failed' ? 'exception' : job.status === 'succeeded' ? 'success' : 'active'" />
          <div v-if="jobQueryFailed" class="mt-3 flex items-center gap-3">
            <span class="text-sm text-red-600">更新任务状态读取失败，任务仍可能在服务器执行。</span>
            <Button size="small" @click="retryJobQuery">重新查询任务</Button>
          </div>
          <Descriptions class="mt-3" :column="1" bordered size="small">
            <Descriptions.Item label="任务 ID">{{ job.jobId }}</Descriptions.Item>
            <Descriptions.Item label="版本变更">{{ job.fromVersion }} → {{ job.toVersion }}</Descriptions.Item>
            <Descriptions.Item label="开始时间">{{ formatDate(job.startedAt) }}</Descriptions.Item>
            <Descriptions.Item label="更新时间">{{ formatDate(job.updatedAt) }}</Descriptions.Item>
            <Descriptions.Item v-if="job.errorCode" label="错误代码">{{ job.errorCode }}</Descriptions.Item>
          </Descriptions>
        </Card>

        <Alert
          v-if="update.updateAvailable && !updateBusy"
          message="已发现新版本，可从此页面提交线上更新任务。"
          show-icon
          type="warning"
          class="mb-4"
        />
        <div class="grid grid-cols-1 gap-4 xl:grid-cols-3">
          <Card title="更新状态">
            <Tag :color="statusColor">{{ statusLabel }}</Tag>
            <div class="mt-3 text-2xl font-semibold">{{ update.currentVersion }}</div>
            <div class="mt-1 text-sm text-gray-500">当前服务版本</div>
          </Card>
          <Card title="最新正式版">
            <div class="text-2xl font-semibold">{{ update.latestVersion }}</div>
            <div class="mt-1 text-sm text-gray-500">{{ update.tag }} · {{ formatDate(update.publishedAt) }}</div>
          </Card>
          <Card title="发行操作">
            <Space wrap>
              <Button :loading="loading" @click="load(false)">重新检查</Button>
              <Button v-if="update.updateAvailable" type="primary" :disabled="updateBusy" @click="showUpdateConfirm">在线更新</Button>
              <a v-if="releaseUrl" :href="releaseUrl" target="_blank" rel="noopener noreferrer">查看 GitHub Release</a>
            </Space>
          </Card>
        </div>

        <Card class="mt-4" title="版本信息">
          <Descriptions :column="1" bordered size="small">
            <Descriptions.Item label="当前版本">{{ update.currentVersion }}</Descriptions.Item>
            <Descriptions.Item label="最新版本">{{ update.latestVersion }}</Descriptions.Item>
            <Descriptions.Item label="Tag">{{ update.tag }}</Descriptions.Item>
            <Descriptions.Item label="发布时间">{{ formatDate(update.publishedAt) }}</Descriptions.Item>
            <Descriptions.Item label="Release 地址">{{ releaseUrl || '未返回有效 GitHub Release 地址' }}</Descriptions.Item>
          </Descriptions>
        </Card>

        <Card class="mt-4" title="发行说明">
          <pre class="m-0 max-h-[28rem] overflow-auto whitespace-pre-wrap break-words">{{ update.notes || '该版本没有发行说明。' }}</pre>
        </Card>

        <Card class="mt-4" title="发行资产">
          <List v-if="assets.length" :data-source="assets" item-layout="horizontal">
            <template #renderItem="{ item }">
              <List.Item>
                <List.Item.Meta :description="`${formatBytes(item.size)} · SHA-256 ${item.digest || '未提供'}`" :title="item.name" />
              </List.Item>
            </template>
          </List>
          <div v-else class="text-sm text-gray-500">没有可显示的发行资产。</div>
        </Card>
      </template>
    </Spin>
    <Modal v-model:open="confirmOpen" title="确认在线更新" :confirm-loading="starting" ok-text="开始更新" cancel-text="取消" @ok="startUpdate">
      <Alert type="warning" show-icon :message="`将从 ${update?.currentVersion} 更新到 ${update?.latestVersion}（${update?.tag}）。服务会按发布包校验、备份、切换并重启，失败时尝试回滚。`" />
    </Modal>
  </Page>
</template>
