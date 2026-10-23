import { requestClient } from '#/api/request';

export type ReleaseChannel = 'beta' | 'stable' | string;

export interface ReleaseProject {
  id?: string;
  key?: string;
  slug?: string;
  name: string;
  repository?: string;
}

export interface ReleaseArtifact {
  name: string;
  key: string;
  size: number;
  sha256: string;
}

export interface ReleaseRecord {
  id?: string;
  project_id?: string;
  project?: string;
  version: string;
  channel: ReleaseChannel;
  repository?: string;
  tag?: string;
  revision: string;
  release_id?: string;
  published_at?: string;
  digest?: string;
  manifest_key?: string;
  status?: string;
  latest?: boolean;
  objectHeadStatus?: string;
  head_status?: string;
  size?: number;
  artifacts?: ReleaseArtifact[];
}

export interface ReleaseJobStatus {
  status: string;
  progress: number;
  jobId: null | string;
  requestId: null | string;
  updatedAt: null | string;
}

export interface ReleaseStorageStatus {
  provider?: string;
  prefix?: string;
  bucket?: string;
  region?: string;
  reachable?: boolean | null;
  connectivityCheckedAt?: null | string;
  read_mode?: string;
  checked_at?: string;
  message?: string;
  projects?: number;
  releases?: number;
  jobs?: Partial<Record<'publish' | 'rollback' | 'validation', ReleaseJobStatus>>;
}

export interface ReleaseAuditDetails {
  status?: string;
  progress?: number;
  before?: Record<string, unknown> | string;
  after?: Record<string, unknown> | string;
  reason?: string;
  digest?: string;
}

export interface ReleaseAuditRecord {
  id?: string;
  action?: string;
  event_type?: string;
  operation?: string;
  status?: string;
  project_id?: string;
  project?: string;
  channel?: ReleaseChannel;
  version?: string;
  object_key?: string;
  digest?: string;
  actor?: string;
  request_id?: string;
  reason?: string;
  progress?: number;
  before?: Record<string, unknown> | string;
  after?: Record<string, unknown> | string;
  created_at?: string;
  message?: string;
  details?: ReleaseAuditDetails;
}

export interface ReleaseCollection<T> {
  items?: T[];
  data?: T[];
  results?: T[];
}

function asList<T>(value: ReleaseCollection<T> | T[] | undefined): T[] {
  if (Array.isArray(value)) return value;
  return value?.items ?? value?.data ?? value?.results ?? [];
}

export async function getReleaseProjectsApi() {
  const response = await requestClient.get<ReleaseCollection<ReleaseProject> | ReleaseProject[]>(
    '/projects',
    { responseReturn: 'body' },
  );
  return asList(response);
}

export async function getReleaseListApi(project: string, channel: string) {
  const response = await requestClient.get<ReleaseCollection<ReleaseRecord> | ReleaseRecord[]>(
    '/releases',
    {
      params: { project, channel },
      responseReturn: 'body',
    },
  );
  return asList(response).map((release) => ({
    ...release,
    head_status: release.objectHeadStatus ?? release.head_status,
  }));
}

export async function getReleaseStorageStatusApi(project: string, channel: string) {
  return requestClient.get<ReleaseStorageStatus>('/release-storage/status', {
    params: { project, channel },
    responseReturn: 'body',
  });
}

export async function getReleaseAuditApi(project: string, channel: string) {
  const response = await requestClient.get<ReleaseAuditRecord[] | ReleaseCollection<ReleaseAuditRecord>>(
    '/release-storage/audit',
    {
      params: { project, channel },
      responseReturn: 'body',
    },
  );
  return asList(response).map((event) => ({
    ...event,
    action: event.event_type ?? event.action ?? event.operation,
    status: event.details?.status ?? event.status,
    progress: event.details?.progress ?? event.progress,
    before: event.details?.before ?? event.before,
    after: event.details?.after ?? event.after,
    reason: event.details?.reason ?? event.reason,
    digest: event.details?.digest ?? event.digest,
  }));
}

function requestId() {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `fuwu-${Date.now()}`;
}

export interface PublishReleasePayload {
  project: string;
  channel: ReleaseChannel;
  version: string;
  tag?: string;
  revision: string;
  staging_ref: string;
}

export async function publishReleaseApi(payload: PublishReleasePayload) {
  const id = requestId();
  return requestClient.post('/release-storage/publish', payload, {
    headers: { 'Idempotency-Key': id, 'X-Request-ID': id },
    responseReturn: 'body',
  });
}

export interface StageRemoteReleasePayload {
  project: string;
  tag: string;
  staging_ref: string;
  assets: string[];
  manifest: Record<string, unknown>;
}

const DEFAULT_STAGE_REMOTE_TIMEOUT_MS = 15 * 60 * 1000;
const MAX_STAGE_REMOTE_TIMEOUT_MS = 60 * 60 * 1000;

/**
 * Remote staging downloads Docker archives on the server and may take several
 * minutes. Keep the timeout configurable for slower deployments while
 * bounding it so a misconfigured Vite variable cannot leave a request open
 * indefinitely.
 */
export function getStageRemoteTimeoutMs(value = import.meta.env.VITE_FUWU_STAGE_REMOTE_TIMEOUT_MS) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed < 60_000) {
    return DEFAULT_STAGE_REMOTE_TIMEOUT_MS;
  }
  return Math.min(Math.floor(parsed), MAX_STAGE_REMOTE_TIMEOUT_MS);
}

export async function stageRemoteReleaseApi(payload: StageRemoteReleasePayload) {
  const id = requestId();
  return requestClient.post('/release-storage/stage-remote', payload, {
    headers: { 'Idempotency-Key': id, 'X-Request-ID': id },
    timeout: getStageRemoteTimeoutMs(),
    responseReturn: 'body',
  });
}

export interface RollbackReleasePayload {
  project: string;
  channel: ReleaseChannel;
  version?: string;
  release_id?: string;
  confirm: true;
  reason: string;
}

export async function rollbackReleaseApi(payload: RollbackReleasePayload) {
  const id = requestId();
  return requestClient.post('/release-storage/rollback', payload, {
    headers: { 'Idempotency-Key': id, 'X-Request-ID': id },
    responseReturn: 'body',
  });
}
