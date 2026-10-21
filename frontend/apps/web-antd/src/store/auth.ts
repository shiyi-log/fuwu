import type { Recordable, UserInfo } from '@vben/types';

import { ref } from 'vue';
import { useRouter } from 'vue-router';

import { LOGIN_PATH } from '@vben/constants';
import { preferences } from '@vben/preferences';
import { resetAllStores, useAccessStore, useUserStore } from '@vben/stores';

import { notification } from 'ant-design-vue';
import { defineStore } from 'pinia';

import { getAccessCodesApi, getUserInfoApi, loginApi, logoutApi } from '#/api';
import { $t } from '#/locales';
import { sanitizeRedirectPath } from '#/router/redirect';

export const useAuthStore = defineStore('auth', () => {
  const accessStore = useAccessStore();
  const userStore = useUserStore();
  const router = useRouter();

  const loginLoading = ref(false);

  /**
   * 异步处理登录操作
   * Asynchronously handle the login process
   * @param params 登录表单数据
   */
  async function authLogin(
    params: Recordable<any>,
    onSuccess?: () => Promise<void> | void,
  ) {
    // 异步处理用户登录操作并获取 accessToken
    let userInfo: null | UserInfo = null;
    try {
      loginLoading.value = true;
      const { accessToken } = await loginApi(params);

      // 如果成功获取到 accessToken
      if (accessToken) {
        accessStore.setAccessToken(accessToken);

        // 获取用户信息并存储到 accessStore 中
        const [fetchUserInfoResult, accessCodes] = await Promise.all([
          fetchUserInfo(),
          getAccessCodesApi(),
        ]);

        userInfo = fetchUserInfoResult;

        userStore.setUserInfo(userInfo);
        accessStore.setAccessCodes(accessCodes);

        if (accessStore.loginExpired) {
          accessStore.setLoginExpired(false);
        } else {
          const redirectPath =
            sanitizeRedirectPath(router.currentRoute.value.query.redirect) ??
            sanitizeRedirectPath(userInfo.homePath) ??
            sanitizeRedirectPath(preferences.app.defaultHomePath) ??
            '/';
          onSuccess
            ? await onSuccess?.()
            : await router.push(redirectPath);
        }

        if (userInfo?.realName) {
          notification.success({
            description: `${$t('authentication.loginSuccessDesc')}:${userInfo?.realName}`,
            duration: 3,
            message: $t('authentication.loginSuccess'),
          });
        }
      }
    } catch {
      // Fail closed if login or initial scope loading fails. Request objects
      // containing passwords/session headers must not reach Vue diagnostics.
      accessStore.setAccessToken(null);
      userStore.setUserInfo(null);
      notification.error({
        description: '登录失败，请检查账号密码或服务连接',
        duration: 3,
        message: '登录失败',
      });
    } finally {
      loginLoading.value = false;
    }

    return {
      userInfo,
    };
  }

  async function logout(redirect: boolean = true) {
    try {
      await logoutApi();
    } catch {
      // 不做任何处理
    }
    resetAllStores();
    accessStore.setLoginExpired(false);

    const redirectPath = sanitizeRedirectPath(router.currentRoute.value.fullPath);
    const defaultPath = sanitizeRedirectPath(preferences.app.defaultHomePath);
    await router.replace({
      path: LOGIN_PATH,
      query: redirect
        && redirectPath && redirectPath !== defaultPath
        ? { redirect: redirectPath }
        : {},
    });
  }

  async function handleSessionExpired() {
    if (router.currentRoute.value.path === LOGIN_PATH) {
      resetAllStores();
      accessStore.setLoginExpired(false);
      return;
    }

    accessStore.setAccessToken(null);
    if (
      preferences.app.loginExpiredMode === 'modal' &&
      accessStore.isAccessChecked
    ) {
      accessStore.setLoginExpired(true);
      return;
    }

    await logout();
  }

  async function fetchUserInfo() {
    const userInfo = await getUserInfoApi();
    userStore.setUserInfo(userInfo);
    return userInfo;
  }

  function $reset() {
    loginLoading.value = false;
  }

  return {
    $reset,
    authLogin,
    fetchUserInfo,
    handleSessionExpired,
    loginLoading,
    logout,
  };
});
