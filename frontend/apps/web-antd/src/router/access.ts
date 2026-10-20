import type { GenerateMenuAndRoutesOptions } from '@vben/types';
import type { Router, RouteRecordRaw } from 'vue-router';

import { generateAccessible } from '@vben/access';
import { BookOpenText, Inbox, List, Settings } from '@vben/icons';
import { useAccessStore, useUserStore } from '@vben/stores';

import { getAccessCodesApi, listProjectsApi } from '#/api';
import { accessRoutes } from '#/router/routes';

async function generateAccess(options: GenerateMenuAndRoutesOptions) {
  const [projects, codes] = await Promise.all([listProjectsApi(), getAccessCodesApi()]);
  useAccessStore().setAccessCodes(codes);
  const projectRoutes: RouteRecordRaw[] = projects.map((project, index) => {
    const path = `/projects/${encodeURIComponent(project.id)}`;
    const children: RouteRecordRaw[] = [];
    const meta = { projectId: project.id, projectName: project.name };
    children.push({
      name: `ProjectDocumentation_${project.id}`, path: `${path}/documentation`,
      component: () => import('#/views/projects/documentation.vue'),
      meta: { ...meta, title: '项目说明', icon: BookOpenText },
    });
    if (codes.includes('release.storage.read')) children.push({
      name: `ProjectReleases_${project.id}`, path: `${path}/releases`,
      component: () => import('#/views/release-management/index.vue'),
      meta: { ...meta, title: '发行版本', icon: Inbox },
    });
    if (codes.includes('logs.read')) children.push({
      name: `ProjectLogs_${project.id}`, path: `${path}/logs`,
      component: () => import('#/views/project-logs/index.vue'),
      meta: { ...meta, title: '任务与日志', icon: List },
    });
    children.push({
      name: `ProjectSettings_${project.id}`, path: `${path}/settings`,
      component: () => import('#/views/projects/settings.vue'),
      meta: { ...meta, title: '项目设置', icon: Settings },
    });
    return { name: `Project_${project.id}`, path, children,
      meta: { title: project.name, icon: Inbox, order: index } };
  });
  // Vben reattaches generated routes to Root. Clear previous scope first so
  // a removed project cannot leave stale navigation or client-side routes.
  const root = options.router.getRoutes().find((route) => route.name === 'Root');
  if (root) root.children.length = 0;
  const routes = options.routes.filter((route) => route.name !== 'Users' || codes.includes('users.manage'));
  return generateAccessible('frontend', {
    ...options, routes: [...routes, ...projectRoutes],
    forbiddenComponent: () => import('#/views/_core/fallback/forbidden.vue'),
  });
}

async function refreshProjectNavigation(router: Router) {
  const access = useAccessStore();
  const result = await generateAccess({ router, routes: accessRoutes, roles: useUserStore().userInfo?.roles ?? [] });
  access.setAccessMenus(result.accessibleMenus);
  access.setAccessRoutes(result.accessibleRoutes);
  access.setIsAccessChecked(true);
}

export { generateAccess, refreshProjectNavigation };
