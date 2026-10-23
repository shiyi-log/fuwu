const UNCERTAIN_HTTP_STATUSES = new Set([408, 504]);
const UNCERTAIN_NETWORK_CODES = new Set([
  'ECONNABORTED',
  'ECONNRESET',
  'ERR_NETWORK',
  'ETIMEDOUT',
]);

type StageRequestError = {
  code?: unknown;
  response?: { status?: unknown };
  status?: unknown;
};

/**
 * Return true only when the client cannot determine whether the synchronous
 * staging job finished. Explicit application failures must require a fresh
 * staging request because the server removes failed job directories.
 */
export function isStageOutcomeUncertain(error: unknown): boolean {
  const candidate = (error ?? {}) as StageRequestError;
  const status = candidate.response?.status ?? candidate.status;
  if (Number.isInteger(status)) {
    return UNCERTAIN_HTTP_STATUSES.has(Number(status));
  }
  return (
    typeof candidate.code === 'string' &&
    UNCERTAIN_NETWORK_CODES.has(candidate.code)
  );
}
