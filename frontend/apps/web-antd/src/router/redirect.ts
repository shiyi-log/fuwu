import { LOGIN_PATH } from '@vben/constants';

const AUTH_PATH = LOGIN_PATH.slice(0, LOGIN_PATH.lastIndexOf('/'));
const MAX_REDIRECT_DECODES = 5;

export function sanitizeRedirectPath(target: unknown): string | undefined {
  if (typeof target !== 'string' || target.length === 0) {
    return undefined;
  }

  let path = target;
  for (let attempt = 0; attempt < MAX_REDIRECT_DECODES && !path.startsWith('/'); attempt += 1) {
    try {
      path = decodeURIComponent(path);
    } catch {
      return undefined;
    }
  }

  if (!path.startsWith('/') || path.startsWith('//') || path.includes('\\')) {
    return undefined;
  }

  const pathname = path.split(/[?#]/, 1)[0] ?? '';
  let canonicalPath: string;
  try {
    canonicalPath = new URL(decodeURIComponent(pathname), 'https://fuwu.invalid').pathname;
  } catch {
    return undefined;
  }

  if (canonicalPath === AUTH_PATH || canonicalPath === LOGIN_PATH || canonicalPath.startsWith(`${LOGIN_PATH}/`)) {
    return undefined;
  }

  return path;
}
