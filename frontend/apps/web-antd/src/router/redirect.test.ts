import { describe, expect, it } from 'vitest';

import { sanitizeRedirectPath } from './redirect';

describe('sanitizeRedirectPath', () => {
  it('rejects nested login URLs from the redirect loop', () => {
    const nestedLogin = encodeURIComponent(
      encodeURIComponent('/auth/login?redirect=%252Fauth%252Flogin'),
    );

    expect(sanitizeRedirectPath(nestedLogin)).toBeUndefined();
    expect(sanitizeRedirectPath('/auth/login?redirect=%252Fprojects')).toBeUndefined();
    expect(sanitizeRedirectPath('/auth')).toBeUndefined();
  });

  it('keeps a valid internal destination after decoding legacy query values', () => {
    const encodedProject = encodeURIComponent(
      encodeURIComponent('/projects?tab=releases'),
    );

    expect(sanitizeRedirectPath(encodedProject)).toBe('/projects?tab=releases');
    expect(sanitizeRedirectPath('/projects?tab=releases')).toBe(
      '/projects?tab=releases',
    );
  });

  it('rejects external, protocol-relative, malformed, and backslash paths', () => {
    expect(sanitizeRedirectPath('https://example.com')).toBeUndefined();
    expect(sanitizeRedirectPath('//example.com/path')).toBeUndefined();
    expect(sanitizeRedirectPath('/\\\\example.com/path')).toBeUndefined();
    expect(sanitizeRedirectPath('%E0%A4%A')).toBeUndefined();
  });
});
