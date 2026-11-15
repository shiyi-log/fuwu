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

  export interface PasswordResetCodeParams {
    email: string;
  }

  export interface PasswordResetParams extends PasswordResetCodeParams {
    code: string;
    newPassword: string;
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

/** Send a one-time password reset code to the account email. */
export async function requestPasswordResetCodeApi(data: AuthApi.PasswordResetCodeParams) {
  try {
    return await requestClient.post('/auth/password-reset/code', data);
  } catch {
    throw new Error('验证码发送失败，请稍后重试');
  }
}

/** Reset the account password with a one-time email code. */
export async function resetPasswordWithCodeApi(data: AuthApi.PasswordResetParams) {
  try {
    return await requestClient.post('/auth/password-reset', data);
  } catch {
    throw new Error('密码重置失败，请检查验证码或稍后重试');
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
