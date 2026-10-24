import { describe, expect, it } from 'vitest';

import {
  isReleaseConflictError,
  rejectWithSanitizedRequestError,
  sanitizeRequestError,
} from './request-errors';

describe('sanitizeRequestError', () => {
  it.each(['ECONNABORTED', 'ETIMEDOUT', 'ERR_NETWORK', 'ECONNRESET'])(
    'preserves safe transport code %s without request data',
    (code) => {
      const error = sanitizeRequestError({
        code,
        config: {
          headers: { Authorization: 'Bearer secret-canary' },
          data: { password: 'password-canary' },
        },
        response: { data: { token: 'token-canary' } },
      });

      expect(error.code).toBe(code);
      expect(error.status).toBeUndefined();
      expect(error.message).toBe('请求未完成，请查看提示或稍后重试');
      expect(JSON.stringify(error)).not.toContain('canary');
      expect(error).not.toHaveProperty('config');
      expect(error).not.toHaveProperty('response');
    },
  );

  it('preserves an HTTP status from an Axios-like response', () => {
    const error = sanitizeRequestError({
      code: 'ERR_BAD_RESPONSE',
      response: { status: 504, data: { secret: 'server-canary' } },
    });

    expect(error.code).toBe('ERR_BAD_RESPONSE');
    expect(error.status).toBe(504);
    expect(JSON.stringify(error)).not.toContain('server-canary');
  });

  it('preserves the allowlisted release conflict code without response data', () => {
    const error = sanitizeRequestError({
      response: {
        status: 409,
        data: {
          code: 'release_conflict',
          message: 'same version already has a different digest',
        },
      },
    });

    expect(error.status).toBe(409);
    expect(error.serverCode).toBe('release_conflict');
    expect(JSON.stringify(error)).not.toContain('different digest');
    expect(error).not.toHaveProperty('response');
  });

  it('drops unknown codes and invalid statuses', () => {
    const error = sanitizeRequestError({
      code: 'CUSTOM_SECRET_CODE',
      status: 700,
      response: { status: 200 },
    });

    expect(error.code).toBeUndefined();
    expect(error.status).toBeUndefined();
  });
});

describe('isReleaseConflictError', () => {
  it('recognizes only the sanitized allowlisted code', () => {
    expect(isReleaseConflictError({ serverCode: 'release_conflict' })).toBe(true);
    expect(isReleaseConflictError({ serverCode: 'project_conflict' })).toBe(false);
    expect(isReleaseConflictError({ status: 409 })).toBe(false);
  });
});

describe('rejectWithSanitizedRequestError', () => {
  it.each([
    [
      {
        code: 'ECONNABORTED',
        message: 'secret message',
        config: { headers: { Authorization: 'Bearer token-canary' } },
      },
      'ECONNABORTED',
      undefined,
    ],
    [
      {
        code: 'ERR_NETWORK',
        config: { data: { password: 'password-canary' } },
      },
      'ERR_NETWORK',
      undefined,
    ],
    [
      { response: { status: 504, data: { secret: 'response-canary' } } },
      undefined,
      504,
    ],
  ])(
    'rejects with bounded transport metadata',
    async (source, expectedCode, expectedStatus) => {
      let rejected: unknown;
      try {
        await rejectWithSanitizedRequestError(source);
      } catch (error) {
        rejected = error;
      }
      expect(rejected).toBeInstanceOf(Error);
      const safeError = rejected as {
        code?: string;
        status?: number;
        message: string;
      };
      expect(safeError.code).toBe(expectedCode);
      expect(safeError.status).toBe(expectedStatus);
      expect(safeError.message).toBe('请求未完成，请查看提示或稍后重试');
      expect(rejected).not.toHaveProperty('config');
      expect(rejected).not.toHaveProperty('response');
      expect(JSON.stringify(rejected)).not.toMatch(/canary|secret message/);
    },
  );
});
