<script setup lang="ts">
import type { ProjectRecord } from '#/api/platform';

import { computed, ref, watch } from 'vue';
import { useRoute } from 'vue-router';
import { Page } from '@vben/common-ui';
import { Alert, Card, Spin } from 'ant-design-vue';

import { listProjectsApi } from '#/api';

const route = useRoute();
const id = computed(() => String(route.meta.projectId || ''));
const project = ref<ProjectRecord>();
const loading = ref(false);
const error = ref('');
let loadRevision = 0;

async function load() {
  const revision = ++loadRevision;
  const selected = id.value;
  if (!selected) return;
  project.value = undefined;
  loading.value = true;
  error.value = '';
  try {
    const item = (await listProjectsApi()).find((entry) => entry.id === selected);
    if (revision !== loadRevision) return;
    if (!item) {
      error.value = '项目不存在或当前账号没有访问权限。';
      return;
    }
    project.value = item;
  } catch {
    if (revision === loadRevision) error.value = '项目说明加载失败。';
  } finally {
    if (revision === loadRevision) loading.value = false;
  }
}

watch(id, load, { immediate: true });
</script>

<template>
  <Page title="项目说明" description="公开 API 与构建方式">
    <Alert v-if="error" :message="error" show-icon type="error" class="mb-4" />
    <Spin :spinning="loading">
      <div v-if="project" class="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Card title="公开 API">
          <pre class="m-0 min-h-32 whitespace-pre-wrap break-words font-sans">{{ project.public_api || '尚未提供公开 API 说明。' }}</pre>
        </Card>
        <Card title="构建方式">
          <pre class="m-0 min-h-32 whitespace-pre-wrap break-words font-mono">{{ project.build_instructions || '尚未提供构建方式说明。' }}</pre>
        </Card>
      </div>
    </Spin>
  </Page>
</template>
