import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  getLatestGitHubReleaseApi,
  getStageRemoteTimeoutMs,
  stageLatestReleaseApi,
  stageRemoteReleaseApi,
  syncLatestGitHubReleaseApi,
  type StageRemoteReleasePayload,
} from './release';
import { requestClient } from './request';

describe('getLatestGitHubReleaseApi', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('discovers a direct latest release response and derives staging assets from the manifest', async () => {
    const get = vi.spyOn(requestClient, 'get').mockResolvedValue({
      repository: 'shiyi-log/telegram-bark-monitor',
      tag: 'v0.1.1',
      version: '0.1.1',
      revision: 'a'.repeat(40),
      releaseId: 421_781_368,
      manifest: {
        schemaVersion: 1,
        assets: {
          docker: [{ path: 'bark-linux-amd64.tar.gz' }],
          python: { path: 'bark.whl' },
        },
      },
    });

    await expect(getLatestGitHubReleaseApi('bark')).resolves.toEqual({
      project: 'bark',
      repository: 'shiyi-log/telegram-bark-monitor',
      tag: 'v0.1.1',
      version: '0.1.1',
      revision: 'a'.repeat(40),
      releaseId: 421_781_368,
      publishedAt: null,
      assets: ['bark-linux-amd64.tar.gz', 'bark.whl'],
      manifest: {
        schemaVersion: 1,
        assets: {
          docker: [{ path: 'bark-linux-amd64.tar.gz' }],
          python: { path: 'bark.whl' },
        },
      },
    });
    expect(get).toHaveBeenCalledWith('/release-storage/latest', {
      params: { project: 'bark' },
      timeout: 70_000,
      responseReturn: 'body',
    });
  });

  it('accepts a nested release response and explicit asset names', async () => {
    vi.spyOn(requestClient, 'get').mockResolvedValue({
      release: {
        project: 'bark',
        repository: 'shiyi-log/telegram-bark-monitor',
        tag: 'v0.1.1',
        version: '0.1.1',
        revision: 'b'.repeat(40),
        releaseId: '421781368',
        assets: [{ name: 'SHA256SUMS' }, { name: 'manifest.json' }, { name: 'bark.whl' }],
        manifest: { assets: {} },
      },
    });

    await expect(getLatestGitHubReleaseApi('bark')).resolves.toMatchObject({
      assets: ['SHA256SUMS', 'manifest.json', 'bark.whl'],
      releaseId: '421781368',
    });
  });

  it('rejects a malformed discovery response', async () => {
    vi.spyOn(requestClient, 'get').mockResolvedValue({ tag: 'v0.1.1', manifest: {} });
    await expect(getLatestGitHubReleaseApi('bark')).rejects.toThrow('latest_release_invalid');
  });
});

describe('syncLatestGitHubReleaseApi', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('requests server-side discovery, staging, validation, and publication', async () => {
    const result = {
      status: 'published',
      latest: {
        repository: 'shiyi-log/fuwu',
        tag: 'v0.0.2',
        version: '0.0.2',
        revision: 'a'.repeat(40),
        releaseId: 123,
        publishedAt: '2026-11-10T01:00:00Z',
        assets: [{ name: 'fuwu.tar.gz', size: 42 }],
        manifest: { assets: [{ path: 'fuwu.tar.gz' }] },
      },
      release: { version: '0.0.2' },
    };
    const post = vi.spyOn(requestClient, 'post').mockResolvedValue(result);
    await expect(syncLatestGitHubReleaseApi('fuwu', 'stable')).resolves.toEqual({
      ...result,
      latest: { ...result.latest, project: 'fuwu', assets: ['fuwu.tar.gz'] },
    });
    const [url, body, config] = post.mock.calls[0] as [string, Record<string, unknown>, Record<string, any>];
    expect(url).toBe('/release-storage/sync-latest');
    expect(body).toEqual({ project: 'fuwu', channel: 'stable' });
    expect(config.timeout).toBe(900_000);
  });

  it.each(['already_published', 'current_newer'])('normalizes upstream asset records for %s responses', async (status) => {
    vi.spyOn(requestClient, 'post').mockResolvedValue({
      status,
      latest: {
        project: 'heyue',
        repository: 'shiyi-log/heyue',
        tag: 'v1.0.14',
        version: '1.0.14',
        revision: 'b'.repeat(40),
        releaseId: '456',
        assets: [{ name: 'heyue.tar.gz', size: 42 }, { name: 'heyue.tar.gz', size: 42 }],
        manifest: { assets: {} },
      },
      current: { version: '1.0.15' },
    });
    await expect(syncLatestGitHubReleaseApi('heyue', 'stable')).resolves.toMatchObject({
      status,
      latest: { project: 'heyue', assets: ['heyue.tar.gz'], publishedAt: null },
      current: { version: '1.0.15' },
    });
  });

  it('rejects malformed latest metadata returned by synchronization', async () => {
    vi.spyOn(requestClient, 'post').mockResolvedValue({ status: 'published', latest: { tag: 'v1.0.14' } });
    await expect(syncLatestGitHubReleaseApi('heyue', 'stable')).rejects.toThrow('latest_release_invalid');
  });
});

describe('stageRemoteReleaseApi', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('uses a bounded long timeout and forwards the exact staging payload', async () => {
    const payload: StageRemoteReleasePayload = {
      project: 'bark',
      tag: 'v0.1.1',
      staging_ref: 'job-bark-v0.1.1',
      assets: ['SHA256SUMS', 'manifest.json', 'monitor.whl'],
      manifest: {
        project: 'bark',
        repository: 'shiyi-log/telegram-bark-monitor',
        version: '0.1.1',
        revision: 'a'.repeat(40),
        releaseId: 421_781_368,
      },
    };
    const post = vi
      .spyOn(requestClient, 'post')
      .mockResolvedValue({ status: 'staged' });

    await stageRemoteReleaseApi(payload);

    expect(post).toHaveBeenCalledTimes(1);
    const [url, body, config] = post.mock.calls[0] as [
      string,
      StageRemoteReleasePayload,
      Record<string, any>,
    ];
    expect(url).toBe('/release-storage/stage-remote');
    expect(body).toEqual(payload);
    expect(config.timeout).toBe(900_000);
    expect(config.headers['Idempotency-Key']).toEqual(expect.any(String));
    expect(config.headers['X-Request-ID']).toBe(
      config.headers['Idempotency-Key'],
    );
    expect(JSON.stringify(body)).not.toContain('secret');
  });
});

describe('stageLatestReleaseApi', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('requests server-side discovery and staging with a long timeout', async () => {
    const post = vi.spyOn(requestClient, 'post').mockResolvedValue({ status: 'staged' });
    await stageLatestReleaseApi({ project: 'bark', staging_ref: 'job-bark-latest', latest: true });
    expect(post).toHaveBeenCalledTimes(1);
    const [url, body, config] = post.mock.calls[0] as [string, Record<string, unknown>, Record<string, any>];
    expect(url).toBe('/release-storage/stage-remote');
    expect(body).toEqual({ project: 'bark', staging_ref: 'job-bark-latest', latest: true });
    expect(config.timeout).toBe(900_000);
    expect(config.headers['Idempotency-Key']).toEqual(expect.any(String));
  });
});

describe('getStageRemoteTimeoutMs', () => {
  it.each([
    [undefined, 900_000],
    ['invalid', 900_000],
    ['59999', 900_000],
    ['60000', 60_000],
    ['3600001', 3_600_000],
    ['999999999', 3_600_000],
  ])('normalizes %s to %s milliseconds', (value, expected) => {
    expect(getStageRemoteTimeoutMs(value)).toBe(expected);
  });
});
