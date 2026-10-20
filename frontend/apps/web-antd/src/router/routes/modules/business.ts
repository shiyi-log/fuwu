import type { RouteRecordRaw } from 'vue-router';
import { Grid, Settings, UserRoundPen } from '@vben/icons';

const routes: RouteRecordRaw[] = [
  {
    name: 'Projects', path: '/projects',
    component: () => import('#/views/projects/index.vue'),
    meta: { title: '项目列表', icon: Grid, order: -100 },
  },
  {
    name: 'Users', path: '/users',
    component: () => import('#/views/users/index.vue'),
    meta: { title: '账号管理', icon: UserRoundPen, order: 1000 },
  },
  {
    name: 'Settings', path: '/settings',
    component: () => import('#/views/settings/index.vue'),
    meta: { title: '系统设置', icon: Settings, order: 1010 },
  },
  {
    name: 'Profile', path: '/profile',
    component: () => import('#/views/_core/profile/index.vue'),
    meta: { title: '个人资料', icon: UserRoundPen, order: 1020 },
  },
];

export default routes;
