import { requestClient } from '#/api/request';

export type ReleaseChannel = 'beta' | 'stable' | string;

export interface ReleaseProject {
  id?: string;
  key?: string;
  slug?: string;
  name: string;
  repository?: string;
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
  head_status?: string;
  size?: number;
}

export interface ReleaseStorageStatus {
  provider?: string;
  prefix?: string;
  bucket?: string;
  region?: string;
  reachable?: boolean;
  read_mode?: string;
  checked_at?: string;
  message?: string;
  projects?: number;
  releases?: number;
}

export interface ReleaseAuditRecord {
  id?: string;
  action?: string;
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

export async function getReleaseListApi(project?: string) {
  const response = await requestClient.get<ReleaseCollection<ReleaseRecord> | ReleaseRecord[]>(
    '/releases',
    {
      params: project ? { project } : undefined,
      responseReturn: 'body',
    },
  );
  return asList(response);
}

export async function getReleaseStorageStatusApi() {
  return requestClient.get<ReleaseStorageStatus>('/release-storage/status', {
    responseReturn: 'body',
  });
}

export async function getReleaseAuditApi(project?: string) {
  const response = await requestClient.get<ReleaseAuditRecord[] | ReleaseCollection<ReleaseAuditRecord>>(
    '/release-storage/audit',
    {
      params: project ? { project } : undefined,
      responseReturn: 'body',
    },
  );
  return asList(response);
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
  release_id?: string;
  staging_ref: string;
  reason?: string;
}

export async function publishReleaseApi(payload: PublishReleasePayload) {
  const id = requestId();
  return requestClient.post('/release-storage/publish', payload, {
    headers: { 'Idempotency-Key': id, 'X-Request-ID': id },
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
