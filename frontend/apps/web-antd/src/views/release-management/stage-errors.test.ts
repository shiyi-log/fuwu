import { describe, expect, it } from 'vitest';

import { isStageOutcomeUncertain } from './stage-errors';

describe('isStageOutcomeUncertain', () => {
  it.each([408, 504])('keeps staging for HTTP %s', (status) => {
    expect(isStageOutcomeUncertain({ status })).toBe(true);
    expect(isStageOutcomeUncertain({ response: { status } })).toBe(true);
  });

  it.each([400, 401, 403, 404, 409, 422, 500, 502, 503])(
    'requires a fresh staging request for HTTP %s',
    (status) => {
      expect(isStageOutcomeUncertain({ status })).toBe(false);
      expect(isStageOutcomeUncertain({ response: { status } })).toBe(false);
    },
  );

  it.each(['ECONNABORTED', 'ECONNRESET', 'ERR_NETWORK', 'ETIMEDOUT'])(
    'keeps staging for transport error %s',
    (code) => {
      expect(isStageOutcomeUncertain({ code })).toBe(true);
    },
  );

  it('does not treat an unclassified response error as uncertain', () => {
    expect(isStageOutcomeUncertain({ code: 'ERR_BAD_RESPONSE' })).toBe(false);
    expect(
      isStageOutcomeUncertain({ code: 'ERR_BAD_RESPONSE', status: 502 }),
    ).toBe(false);
    expect(isStageOutcomeUncertain({ code: 'CUSTOM_ERROR' })).toBe(false);
    expect(isStageOutcomeUncertain(null)).toBe(false);
  });
});
