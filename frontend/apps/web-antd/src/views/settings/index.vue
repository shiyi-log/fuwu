<script setup lang="ts">
import type { GithubAuthorization } from '#/api/platform';
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue';
import { useAccess } from '@vben/access';
import { Page } from '@vben/common-ui';
import { Alert, Button, Card, Descriptions, Form, Input, InputNumber, message, Select, Space, Spin, Tag } from 'ant-design-vue';
import { authorizeGithubApi, getGithubAuthorizationApi, getSystemSettingsApi, revokeGithubAuthorizationApi, updateObjectStorageSettingsApi, updateSystemSettingsApi } from '#/api';

const { hasAccessByCodes } = useAccess();
const canManage = computed(() => hasAccessByCodes(['settings.manage']));
const loading = ref(false); const saving = ref(false); const error = ref('');
const storageSaving = ref(false);
const model = reactive({ appName: 'fuwu', timezone: 'Asia/Shanghai', defaultChannel: 'stable' as 'beta' | 'stable', logRetentionDays: 90, version: 'unknown' });
const metadata = ref<Record<string, boolean | null | number | string>>({});
const storage = reactive({ bucket: '', region: '', endpoint: '', prefix: '', credentialsConfigured: false, credentialSource: '', accessKeyHint: null as string | null });
const credentials = reactive({ accessKeyId: '', secretAccessKey: '', sessionToken: '' });
const github = ref<GithubAuthorization>(); const githubToken = ref(''); const githubSaving = ref(false);
const labels: Record<string, string> = { version: '服务版本', provider: '对象存储 Provider', publicReadMode: '公共读取方式', repository: '数据 Repository', singleInstance: '单实例模式' };
const canSubmitCredentials = computed(() => {
  if (typeof window === 'undefined') return false;
  return window.location.protocol === 'https:' || ['localhost', '127.0.0.1'].includes(window.location.hostname);
});
const hasCredentialInput = computed(() => Object.values(credentials).some(Boolean));

function clearCredentials() {
  credentials.accessKeyId = '';
  credentials.secretAccessKey = '';
  credentials.sessionToken = '';
}

async function load() {
  loading.value = true;
  try {
    const data = await getSystemSettingsApi();
    Object.assign(model, { appName: data.appName, timezone: data.timezone, defaultChannel: data.defaultChannel, logRetentionDays: data.logRetentionDays, version: data.version });
    metadata.value = data.readOnly;
    Object.assign(storage, data.storage || {});
    github.value = canManage.value ? await getGithubAuthorizationApi() : undefined;
    error.value = '';
  }
  catch { error.value = '系统设置加载失败。'; }
  finally { loading.value = false; }
}
async function save() {
  if (!model.appName.trim() || !model.timezone.trim() || model.logRetentionDays < 7 || model.logRetentionDays > 3650) { message.warning('请填写名称、有效时区和 7–3650 天的日志保留期'); return; }
  saving.value = true;
  try { await updateSystemSettingsApi({ ...model, appName: model.appName.trim(), timezone: model.timezone.trim() }); message.success('系统设置已保存'); await load(); }
  catch { /* Shared client handles safe errors. */ }
  finally { saving.value = false; }
}

async function saveStorage() {
  if (!canManage.value) return;
  if (!storage.bucket.trim() || !storage.region.trim() || !storage.prefix.trim()) {
    message.warning('请填写 Bucket、区域和对象前缀。');
    return;
  }
  if (hasCredentialInput.value && !canSubmitCredentials.value) {
    message.error('当前连接不安全。请通过 HTTPS 或 localhost 页面提交存储凭据。');
    return;
  }
  if (hasCredentialInput.value && (!credentials.accessKeyId.trim() || !credentials.secretAccessKey)) {
    message.warning('更新凭据时，Access Key ID 和 Secret Access Key 必须同时填写。');
    return;
  }
  storageSaving.value = true;
  try {
    const result = await updateObjectStorageSettingsApi({
      bucket: storage.bucket.trim(),
      region: storage.region.trim(),
      endpoint: storage.endpoint.trim(),
      prefix: storage.prefix.trim(),
      accessKeyId: credentials.accessKeyId,
      secretAccessKey: credentials.secretAccessKey,
      sessionToken: credentials.sessionToken,
    });
    Object.assign(storage, result);
    clearCredentials();
    message.success('对象存储设置已保存；凭据不会回显。');
  } catch {
    /* Shared client reports only the server's fixed safe error. */
  } finally {
    storageSaving.value = false;
  }
}

async function saveGithub() {
  if (!githubToken.value.trim()) { message.warning('请输入 GitHub 访问令牌'); return; }
  githubSaving.value = true;
  try { github.value = await authorizeGithubApi(githubToken.value.trim()); githubToken.value = ''; message.success('GitHub 账号已授权，令牌仅保存在服务端受保护文件中'); }
  catch { /* Shared client displays safe server errors. */ }
  finally { githubSaving.value = false; }
}
async function revokeGithub() {
  githubSaving.value = true;
  try { github.value = await revokeGithubAuthorizationApi(); message.success('GitHub 授权已撤销'); }
  catch { /* Shared client displays safe server errors. */ }
  finally { githubSaving.value = false; }
}

onMounted(load);
onBeforeUnmount(clearCredentials);
</script>
<template>
  <Page title="系统设置" description="管理服务通用配置与只读运行信息">
    <Alert v-if="error" :message="error" type="error" show-icon class="mb-4" />
    <Spin :spinning="loading"><div class="grid grid-cols-1 gap-4 xl:grid-cols-2"><Card title="通用配置"><Form layout="vertical"><Form.Item label="服务名称" required><Input v-model:value="model.appName" :disabled="!canManage" :maxlength="120" /></Form.Item><Form.Item label="时区" required><Input v-model:value="model.timezone" :disabled="!canManage" placeholder="Asia/Shanghai" /></Form.Item><Form.Item label="默认发行频道"><Select v-model:value="model.defaultChannel" :disabled="!canManage" :options="[{ value: 'stable', label: 'stable' }, { value: 'beta', label: 'beta' }]" /></Form.Item><Form.Item label="运营日志保留天数"><InputNumber v-model:value="model.logRetentionDays" :disabled="!canManage" :min="7" :max="3650" :precision="0" /></Form.Item><Alert message="保留期只应用于运营日志，不会删除不可变的发行审计。" show-icon type="info" class="mb-4" /><Button v-if="canManage" type="primary" :loading="saving" @click="save">保存设置</Button></Form></Card><Card title="只读运行信息"><Descriptions :column="1" bordered><Descriptions.Item label="服务版本">{{ model.version }}</Descriptions.Item><Descriptions.Item v-for="(value, key) in metadata" :key="key" :label="labels[key] || key">{{ typeof value === 'boolean' ? value ? '是' : '否' : value ?? '—' }}</Descriptions.Item></Descriptions></Card>
      <Card v-if="canManage" title="GitHub 账号授权" class="xl:col-span-2">
        <div class="mb-3 text-sm text-gray-500">授权全局 GitHub 账号后，远端发行下载可以读取 GitHub 仓库的私有 Release。令牌只会发送到服务端并保存在受保护文件中。</div>
        <div class="mb-3"><Tag :color="github?.authorized ? 'green' : 'default'">{{ github?.authorized ? `已授权：${github.login}` : '未授权' }}</Tag><span v-if="github?.authorizedAt" class="ml-2 text-xs text-gray-400">{{ github.authorizedAt }}</span></div>
        <Form layout="vertical">
          <Form.Item label="GitHub Personal Access Token">
            <Input.Password v-model:value="githubToken" :maxlength="512" autocomplete="new-password" placeholder="仅用于校验和远端下载，不会回显" />
          </Form.Item>
          <Space>
            <Button type="primary" :loading="githubSaving" @click="saveGithub">授权账号</Button>
            <Button v-if="github?.authorized" danger :loading="githubSaving" @click="revokeGithub">撤销授权</Button>
          </Space>
        </Form>
      </Card>
      <Card title="对象存储配置" class="xl:col-span-2">
        <Alert v-if="!canSubmitCredentials" class="mb-4" message="当前页面不是安全连接。可以保存非敏感配置；提交存储凭据需使用 HTTPS 或 localhost。" show-icon type="warning" />
        <Form layout="vertical">
          <div class="grid grid-cols-1 gap-x-4 md:grid-cols-2 xl:grid-cols-4">
            <Form.Item label="Bucket" required><Input v-model:value="storage.bucket" :disabled="!canManage" autocomplete="off" /></Form.Item>
            <Form.Item label="区域" required><Input v-model:value="storage.region" :disabled="!canManage" autocomplete="off" placeholder="your-region-1" /></Form.Item>
            <Form.Item label="S3 兼容 Endpoint"><Input v-model:value="storage.endpoint" :disabled="!canManage" autocomplete="off" placeholder="留空使用 AWS S3 默认地址" /></Form.Item>
            <Form.Item label="对象前缀" required><Input v-model:value="storage.prefix" :disabled="!canManage" autocomplete="off" placeholder="releases" /></Form.Item>
          </div>
          <div class="mb-3 flex flex-wrap items-center gap-2 text-sm text-gray-500">
            <span>凭据状态：{{ storage.credentialsConfigured ? '已配置' : '未配置' }}</span>
            <span v-if="storage.credentialSource">· 来源：{{ storage.credentialSource }}</span>
            <span v-if="canManage && storage.credentialsConfigured">· Access Key ID：{{ storage.accessKeyHint || '已配置' }}</span>
            <span v-if="canManage && storage.credentialsConfigured">· Secret Access Key：已配置，不回显</span>
            <span>· 输入框始终为空，保存后不会回显凭据</span>
          </div>
          <div class="grid grid-cols-1 gap-x-4 md:grid-cols-3">
            <Form.Item label="Access Key ID"><Input v-model:value="credentials.accessKeyId" :disabled="!canManage || !canSubmitCredentials" autocomplete="off" /></Form.Item>
            <Form.Item label="Secret Access Key"><Input.Password v-model:value="credentials.secretAccessKey" :disabled="!canManage || !canSubmitCredentials" autocomplete="new-password" /></Form.Item>
            <Form.Item label="Session Token（可选）"><Input.Password v-model:value="credentials.sessionToken" :disabled="!canManage || !canSubmitCredentials" autocomplete="new-password" /></Form.Item>
          </div>
          <Button v-if="canManage" type="primary" :loading="storageSaving" @click="saveStorage">保存对象存储设置</Button>
        </Form>
      </Card>
    </div></Spin>
  </Page>
</template>
