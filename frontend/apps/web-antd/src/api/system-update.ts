import { requestClient } from '#/api/request';

export interface SystemUpdateAsset {
  name: string;
  size: number;
  digest: string | null;
}

export interface SystemUpdateResponse {
  currentVersion: string;
  latestVersion: string;
  updateAvailable: boolean;
  tag: string;
  publishedAt: string | null;
  releaseUrl: string;
  notes: string;
  assets: SystemUpdateAsset[];
}

export type SystemUpdateJobStatus =
  | 'queued'
  | 'downloading'
  | 'verifying'
  | 'preparing'
  | 'backing_up'
  | 'switching'
  | 'restarting'
  | 'health_check'
  | 'succeeded'
  | 'failed'
  | 'rolling_back'
  | 'rolled_back';

export interface SystemUpdateJob {
  jobId: string;
  status: SystemUpdateJobStatus;
  progress: number;
  message: string;
  errorCode: string | null;
  fromVersion: string;
  toVersion: string;
  startedAt: string | null;
  updatedAt: string | null;
}

function unwrapJob(response: SystemUpdateJob | { job: SystemUpdateJob }): SystemUpdateJob {
  return 'job' in response ? response.job : response;
}

export function getSystemUpdateApi() {
  return requestClient.get<SystemUpdateResponse>('/system-update', {
    responseReturn: 'body',
  });
}

export async function startSystemUpdateApi(tag: string) {
  const response = await requestClient.post<SystemUpdateJob | { job: SystemUpdateJob }>(
    '/system-update',
    { tag },
    { responseReturn: 'body' },
  );
  return unwrapJob(response);
}

export async function getSystemUpdateJobApi(jobId: string) {
  const response = await requestClient.get<SystemUpdateJob | { job: SystemUpdateJob }>(
    `/system-update/jobs/${encodeURIComponent(jobId)}`,
    { responseReturn: 'body' },
  );
  return unwrapJob(response);
}
