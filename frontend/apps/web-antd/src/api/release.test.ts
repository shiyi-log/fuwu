import { beforeEach, describe, expect, it, vi } from 'vitest';

import { requestClient } from './request';
import {
  getStageRemoteTimeoutMs,
  stageRemoteReleaseApi,
  type StageRemoteReleasePayload,
} from './release';

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
        releaseId: 421781368,
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
