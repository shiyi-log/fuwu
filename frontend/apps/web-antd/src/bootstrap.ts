import { createApp, watchEffect } from 'vue';

import { registerAccessDirective } from '@vben/access';
import { registerLoadingDirective } from '@vben/common-ui/es/loading';
import { addIcon } from '@vben/icons';
import { preferences } from '@vben/preferences';
import { initStores } from '@vben/stores';
import '@vben/styles';
import '@vben/styles/antd';

import { useTitle } from '@vueuse/core';

import { $t, setupI18n } from '#/locales';

import { initComponentAdapter } from './adapter/component';
import { initSetupVbenForm } from './adapter/form';
import App from './app.vue';
import { router } from './router';

async function bootstrap(namespace: string) {
  // Bundle the few Iconify icons used by the Vben shell locally so the
  // management console never needs an external icon service at runtime.
  addIcon('mdi:keyboard-esc', {
    width: 24, height: 24,
    body: '<path fill="currentColor" d="M1 7h6v2H3v2h4v2H3v2h4v2H1zm8 0h6v2h-4v2h4v6H9v-2h4v-2H9zm8 0h6v2h-4v6h4v2h-6z"/>',
  });
  addIcon('ep:fold', {
    width: 24, height: 24,
    body: '<path fill="none" stroke="currentColor" stroke-width="2" d="M3 5h18M3 19h18M12 10h9M12 14h9M8 9l-3 3 3 3"/>',
  });
  addIcon('ep:expand', {
    width: 24, height: 24,
    body: '<path fill="none" stroke="currentColor" stroke-width="2" d="M3 5h18M3 19h18M12 10h9M12 14h9M5 9l3 3-3 3"/>',
  });
  // 初始化组件适配器
  await initComponentAdapter();

  // 初始化表单组件
  await initSetupVbenForm();

  // // 设置弹窗的默认配置
  // setDefaultModalProps({
  //   fullscreenButton: false,
  // });
  // // 设置抽屉的默认配置
  // setDefaultDrawerProps({
  //   zIndex: 1020,
  // });

  const app = createApp(App);

  // 注册v-loading指令
  registerLoadingDirective(app, {
    loading: 'loading', // 在这里可以自定义指令名称，也可以明确提供false表示不注册这个指令
    spinning: 'spinning',
  });

  // 国际化 i18n 配置
  await setupI18n(app);

  // 配置 pinia-tore
  await initStores(app, { namespace });

  // 安装权限指令
  registerAccessDirective(app);

  // 初始化 tippy
  const { initTippy } = await import('@vben/common-ui/es/tippy');
  initTippy(app);

  // 配置路由及路由守卫
  app.use(router);

  // 配置Motion插件
  const { MotionPlugin } = await import('@vben/plugins/motion');
  app.use(MotionPlugin);

  // 动态更新标题
  watchEffect(() => {
    if (preferences.app.dynamicTitle) {
      const routeTitle = router.currentRoute.value.meta?.title;
      const pageTitle =
        (routeTitle ? `${$t(routeTitle)} - ` : '') + preferences.app.name;
      useTitle(pageTitle);
    }
  });

  app.mount('#app');
}

export { bootstrap };
