const SAFE_REQUEST_CODES = new Set([
  'ECONNABORTED',
  'ECONNRESET',
  'ERR_BAD_RESPONSE',
  'ERR_NETWORK',
  'ETIMEDOUT',
]);
const SAFE_SERVER_CODES = new Set([
  'github_authorization_required',
  'github_repository_access_denied',
  'github_token_invalid',
  'github_unavailable',
  'immutable_object_conflict',
  'remote_asset_missing',
  'remote_asset_unavailable',
  'remote_checksums_invalid',
  'remote_checksums_missing',
  'remote_checksum_mismatch',
  'remote_manifest_missing',
  'remote_manifest_unavailable',
  'remote_release_not_found',
  'remote_release_unavailable',
  'remote_size_mismatch',
  'release_conflict',
]);

export type SafeRequestError = Error & {
  code?: string;
  serverCode?: string;
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
    response?: { data?: { code?: unknown }; status?: unknown };
    status?: unknown;
    serverCode?: unknown;
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
  const serverCode = candidate?.response?.data?.code ?? candidate?.serverCode;
  if (typeof serverCode === 'string' && SAFE_SERVER_CODES.has(serverCode)) {
    safeError.serverCode = serverCode;
  }
  return safeError;
}

export function isReleaseConflictError(error: unknown): boolean {
  const candidate = error as { serverCode?: unknown } | null;
  return (
    candidate?.serverCode === 'release_conflict' ||
    candidate?.serverCode === 'immutable_object_conflict'
  );
}

export function rejectWithSanitizedRequestError(
  error: unknown,
): Promise<never> {
  return Promise.reject(sanitizeRequestError(error));
}
