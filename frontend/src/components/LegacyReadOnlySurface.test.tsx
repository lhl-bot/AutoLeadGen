import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { apiFetch } from '@/lib/utils';
import LegacyReadOnlySurface from './LegacyReadOnlySurface';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('LegacyReadOnlySurface', () => {
  it('disables native controls and blocks role-button actions', async () => {
    const nativeAction = vi.fn();
    const customAction = vi.fn();
    const user = userEvent.setup();

    render(
      <LegacyReadOnlySurface>
        <button type="button" onClick={nativeAction}>删除</button>
        <div role="button" tabIndex={0} onClick={customAction}>批量发送</div>
        <a href="#details">查看详情</a>
      </LegacyReadOnlySurface>,
    );

    expect(screen.getByRole('button', { name: '删除' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '批量发送' })).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByRole('link', { name: '查看详情' })).not.toHaveAttribute('aria-disabled');

    await user.click(screen.getByRole('button', { name: '批量发送' }));
    expect(nativeAction).not.toHaveBeenCalled();
    expect(customAction).not.toHaveBeenCalled();
  });

  it.each([
    '/api/channels/accounts?sync=true',
    '/api/channels/accounts?sync=1',
    '/api/api-usage/summary',
    '/api/workflows/18/health',
    '/api/health/status?external=yes',
  ])('blocks side-effectful legacy GET before it reaches the network: %s', async endpoint => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}'));
    render(<LegacyReadOnlySurface><p>Legacy data</p></LegacyReadOnlySurface>);

    const response = await apiFetch(endpoint);
    expect(response.status).toBe(409);
    await expect(response.json()).resolves.toMatchObject({ detail: { code: 'LEGACY_API_READ_ONLY' } });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('blocks programmatic legacy writes while preserving harmless reads and navigation', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}'));
    render(
      <LegacyReadOnlySurface>
        <a href="#details">查看详情</a>
        <section id="details">Details</section>
      </LegacyReadOnlySurface>,
    );

    const blocked = await apiFetch('/api/leads/22/rate', { method: 'POST' });
    const response = await apiFetch('/api/leads?limit=25');
    await user.click(screen.getByRole('link', { name: '查看详情' }));

    expect(blocked.status).toBe(409);
    expect(response.ok).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0][0])).toContain('/api/leads?limit=25');
    expect(window.location.hash).toBe('#details');
  });

  it('keeps session logout reachable from a legacy read-only page', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(null, { status: 204 }));
    render(<LegacyReadOnlySurface><p>Legacy data</p></LegacyReadOnlySurface>);

    const response = await apiFetch('/api/auth/logout', { method: 'POST' });

    expect(response.status).toBe(204);
    expect(fetchMock).toHaveBeenCalledOnce();
  });
});
