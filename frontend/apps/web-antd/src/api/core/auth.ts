import { useAccessStore } from '@vben/stores';

import { baseRequestClient, requestClient } from '#/api/request';

export namespace AuthApi {
  /** 登录接口参数 */
  export interface LoginParams {
    username?: string;
    password?: string;
  }

  /** 登录接口返回值 */
  export interface LoginResult {
    accessToken: string;
    expiresAt: number;
  }

  export interface RefreshTokenResult {
    data: string;
    status: number;
  }
}

/**
 * 登录
 */
export async function loginApi(data: AuthApi.LoginParams) {
  try {
    return await requestClient.post<AuthApi.LoginResult>('/auth/login', data);
  } catch {
    // Do not let an Axios error containing the submitted password
    // escape into Vue's unhandled-event logging or a caller's diagnostics.
    throw new Error('登录失败，请检查账号密码或服务连接');
  }
}

/**
 * 刷新accessToken
 */
export async function refreshTokenApi() {
  return baseRequestClient.post<AuthApi.RefreshTokenResult>('/auth/refresh', {
    withCredentials: true,
  });
}

/**
 * 退出登录
 */
export async function logoutApi() {
  // Bypass the reauthentication interceptor: an expired session must still be
  // removable locally without recursively triggering another logout request.
  const accessStore = useAccessStore();
  return baseRequestClient.post(
    '/auth/logout',
    {},
    {
      headers: accessStore.accessToken
        ? { Authorization: `Bearer ${accessStore.accessToken}` }
        : undefined,
    },
  );
}

/**
 * 获取用户权限码
 */
export async function getAccessCodesApi() {
  return requestClient.get<string[]>('/auth/codes');
}
