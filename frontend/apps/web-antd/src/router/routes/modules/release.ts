import type { RouteRecordRaw } from 'vue-router';

const routes: RouteRecordRaw[] = [
  {
    name: 'ReleaseManagement',
    path: '/release-management',
    component: () => import('#/views/release-management/index.vue'),
    meta: {
      icon: 'lucide:package-check',
      order: 20,
      title: '发行管理',
    },
  },
];

export default routes;
