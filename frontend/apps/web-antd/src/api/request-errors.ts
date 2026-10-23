const SAFE_REQUEST_CODES = new Set([
  'ECONNABORTED',
  'ECONNRESET',
  'ERR_BAD_RESPONSE',
  'ERR_NETWORK',
  'ETIMEDOUT',
]);

export type SafeRequestError = Error & {
  code?: string;
  status?: number;
};

/**
 * Keep only bounded transport metadata for UI recovery paths.
 *
 * The original Axios error can contain request config, authorization headers,
 * form data, and server response text. None of those values may cross the
 * request boundary into UI code or logs.
 */
export function sanitizeRequestError(error: unknown): SafeRequestError {
  const safeError = new Error(
    '请求未完成，请查看提示或稍后重试',
  ) as SafeRequestError;
  const candidate = error as {
    code?: unknown;
    response?: { status?: unknown };
    status?: unknown;
  } | null;

  if (
    typeof candidate?.code === 'string' &&
    SAFE_REQUEST_CODES.has(candidate.code)
  ) {
    safeError.code = candidate.code;
  }
  const status = candidate?.response?.status ?? candidate?.status;
  if (
    Number.isInteger(status) &&
    Number(status) >= 400 &&
    Number(status) <= 599
  ) {
    safeError.status = Number(status);
  }
  return safeError;
}

export function rejectWithSanitizedRequestError(
  error: unknown,
): Promise<never> {
  return Promise.reject(sanitizeRequestError(error));
}
