import { requestClient } from '#/api/request';

export interface ProjectRecord {
  id: string;
  name: string;
  repository: string;
  description?: string;
  public_api?: string;
  build_instructions?: string;
  active: boolean;
  created_at?: string;
}

export interface GithubAuthorization {
  authorized: boolean;
  login?: string | null;
  node_id?: string | null;
  scopes?: string[];
  authorizedAt?: string | null;
}

export interface AccountRecord {
  id: string;
  username: string;
  displayName: string;
  active: boolean;
  roles: string[];
  permissions: string[];
  directPermissions?: string[];
  project_ids: null | string[];
  createdAt?: string;
}

export interface RoleRecord {
  id: string;
  name: string;
  permissions: string[];
}

export interface OperationLog {
  id: number | string;
  created_at: string;
  level: string;
  category: string;
  action: string;
  actor: string;
  request_id: string;
  project_id?: string;
  channel?: string;
  status_code?: number;
  method?: string;
  route?: string;
  duration_ms?: number;
}

export interface SystemSettings {
  appName: string;
  timezone: string;
  defaultChannel: 'beta' | 'stable';
  logRetentionDays: number;
  readOnly: Record<string, boolean | null | number | string>;
  storage: ObjectStorageSettings;
}

export interface ObjectStorageSettings {
  bucket: string;
  region: string;
  endpoint: string;
  prefix: string;
  credentialsConfigured: boolean;
  credentialSource: string;
  accessKeyHint?: string | null;
}

export interface ObjectStorageSettingsPayload {
  bucket: string;
  region: string;
  endpoint: string;
  prefix: string;
  accessKeyId: string;
  secretAccessKey: string;
  sessionToken: string;
}

export async function listProjectsApi() {
  const data = await requestClient.get<{ items: ProjectRecord[] }>('/projects', { responseReturn: 'body' });
  return data.items;
}

export function createProjectApi(data: Pick<ProjectRecord, 'description' | 'id' | 'name' | 'repository'>) {
  return requestClient.post('/projects', data, { responseReturn: 'body' });
}

export function updateProjectApi(id: string, data: Partial<Pick<ProjectRecord, 'active' | 'build_instructions' | 'description' | 'name' | 'public_api'>>) {
  return requestClient.request(`/projects/${encodeURIComponent(id)}`, { data, method: 'PATCH', responseReturn: 'body' });
}

export function getGithubAuthorizationApi(id: string) {
  return requestClient.get<GithubAuthorization>(`/projects/${encodeURIComponent(id)}/github-authorization`, { responseReturn: 'body' });
}

export function authorizeGithubApi(id: string, token: string) {
  return requestClient.post<GithubAuthorization>(`/projects/${encodeURIComponent(id)}/github-authorization`, { token }, { responseReturn: 'body' });
}

export function revokeGithubAuthorizationApi(id: string) {
  return requestClient.request<GithubAuthorization>(`/projects/${encodeURIComponent(id)}/github-authorization`, { method: 'DELETE', responseReturn: 'body' });
}

export async function listAccountsApi() {
  const data = await requestClient.get<{ items: AccountRecord[] }>('/users', { responseReturn: 'body' });
  return data.items;
}

export function createAccountApi(data: Record<string, unknown>) {
  return requestClient.post('/users', data, { responseReturn: 'body' });
}

export function updateAccountApi(id: string, data: Record<string, unknown>) {
  return requestClient.request(`/users/${encodeURIComponent(id)}`, { data, method: 'PATCH', responseReturn: 'body' });
}

export function resetAccountPasswordApi(id: string, password: string) {
  return requestClient.post(`/users/${encodeURIComponent(id)}/password`, { password }, { responseReturn: 'body' });
}

export function changePasswordApi(currentPassword: string, newPassword: string) {
  return requestClient.post('/auth/password', { currentPassword, newPassword }, { responseReturn: 'body' });
}

export async function listRolesApi() {
  const data = await requestClient.get<{ items: RoleRecord[] }>('/roles', { responseReturn: 'body' });
  return data.items;
}

export async function listLogsApi(params: { category?: string; channel?: string; level?: string; limit?: number; project?: string }) {
  const data = await requestClient.get<{ items: OperationLog[] }>('/logs', { params, responseReturn: 'body' });
  return data.items;
}

export function getSystemSettingsApi() {
  return requestClient.get<SystemSettings>('/settings', { responseReturn: 'body' });
}

export function updateSystemSettingsApi(data: Omit<SystemSettings, 'readOnly' | 'storage'>) {
  return requestClient.request('/settings', { data, method: 'PATCH', responseReturn: 'body' });
}

export function updateObjectStorageSettingsApi(data: ObjectStorageSettingsPayload) {
  return requestClient.request<ObjectStorageSettings>('/settings/storage', {
    data,
    method: 'PATCH',
    responseReturn: 'body',
  });
}
