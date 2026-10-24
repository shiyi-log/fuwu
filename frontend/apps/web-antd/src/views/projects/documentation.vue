<script setup lang="ts">
import type { ProjectDocumentation, ProjectRecord } from '#/api/platform';

import { computed, ref, watch } from 'vue';
import { useRoute } from 'vue-router';
import { Page } from '@vben/common-ui';
import { Alert, Card, Spin } from 'ant-design-vue';

import { getProjectDocumentationApi, listProjectsApi } from '#/api';

const route = useRoute();
const id = computed(() => String(route.meta.projectId || ''));
const project = ref<ProjectRecord>();
const documentation = ref<ProjectDocumentation>();
const loading = ref(false);
const error = ref('');
let loadRevision = 0;

async function load() {
  const revision = ++loadRevision;
  const selected = id.value;
  if (!selected) return;
  project.value = undefined;
  documentation.value = undefined;
  loading.value = true;
  error.value = '';
  try {
    const [items, source] = await Promise.all([listProjectsApi(), getProjectDocumentationApi(selected)]);
    if (revision !== loadRevision) return;
    const item = items.find((entry) => entry.id === selected);
    if (!item) {
      error.value = '项目不存在或当前账号没有访问权限。';
      return;
    }
    project.value = item;
    documentation.value = source;
  } catch {
    if (revision === loadRevision) error.value = '原项目说明加载失败，请检查项目仓库和 GitHub 授权。';
  } finally {
    if (revision === loadRevision) loading.value = false;
  }
}

watch(id, load, { immediate: true });
</script>

<template>
  <Page title="项目说明" description="读取绑定代码仓库的原项目 README">
    <Alert v-if="error" :message="error" show-icon type="error" class="mb-4" />
    <Spin :spinning="loading">
      <div v-if="project && documentation" class="grid grid-cols-1 gap-4">
        <Card title="原项目说明">
          <template #extra>
            <a v-if="documentation.sourceUrl" :href="documentation.sourceUrl" target="_blank" rel="noopener noreferrer">在 GitHub 查看</a>
          </template>
          <div class="mb-3 text-sm text-gray-500">
            {{ documentation.repository }} / {{ documentation.path }}
          </div>
          <pre class="m-0 max-h-[70vh] overflow-auto whitespace-pre-wrap break-words font-mono">{{ documentation.content || '原项目暂未提供 README 说明。' }}</pre>
        </Card>
      </div>
    </Spin>
  </Page>
</template>
