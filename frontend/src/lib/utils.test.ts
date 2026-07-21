import { afterEach, describe, expect, it, vi } from 'vitest';
import { apiFetch, isAbortError } from './utils';

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  document.cookie = 'autoleadgen_csrf=; Max-Age=0; path=/';
});

describe('isAbortError', () => {
  it('recognizes browser abort variants without hiding unrelated failures', () => {
    expect(isAbortError(new DOMException('The operation was aborted.', 'AbortError'))).toBe(true);
    expect(isAbortError(new TypeError('signal is aborted without reason'))).toBe(true);
    expect(isAbortError(new Error('request failed'))).toBe(false);
  });

  it('sends browser cookies and the CSRF header on state-changing requests', async () => {
    document.cookie = 'autoleadgen_csrf=csrf-test-value; path=/';
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(null, { status: 204 }));

    const response = await apiFetch('/api/v2/settings/channels', {
      method: 'PUT',
      body: JSON.stringify({}),
    });

    expect(response.status).toBe(204);
    const request = fetchMock.mock.calls[0][1];
    expect(request?.credentials).toBe('include');
    expect(new Headers(request?.headers).get('X-CSRF-Token')).toBe('csrf-test-value');
  });
});
