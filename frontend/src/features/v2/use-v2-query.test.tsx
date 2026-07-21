import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { DataEnvelope } from './types';
import { useV2Query } from './use-v2-query';

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}

describe('useV2Query refresh behavior', () => {
  it('keeps the last live snapshot mounted while a refresh is in flight', async () => {
    // GIVEN: One loaded authoring snapshot and a refresh request that remains pending.
    const user = userEvent.setup();
    const pending = deferred<DataEnvelope<string>>();
    const loader = vi.fn()
      .mockResolvedValueOnce({ data: 'revision diff visible', source: 'live', observedAt: '2026-07-16T00:00:00Z' })
      .mockReturnValueOnce(pending.promise);

    function Probe() {
      const { result, loading, refresh } = useV2Query<string>(loader);
      if (loading || !result) return <p>loading</p>;
      return <div><p>{result.data}</p><button type="button" onClick={refresh}>refresh</button></div>;
    }
    render(<Probe />);
    await screen.findByText('revision diff visible');

    // WHEN: The parent refreshes immediately after persisting a Campaign DRAFT.
    await user.click(screen.getByRole('button', { name: 'refresh' }));

    // THEN: The existing child remains mounted, preserving its reviewed diff state.
    expect(screen.getByText('revision diff visible')).toBeInTheDocument();
    expect(screen.queryByText('loading')).not.toBeInTheDocument();
    pending.resolve({ data: 'refreshed', source: 'live', observedAt: '2026-07-16T00:01:00Z' });
    expect(await screen.findByText('refreshed')).toBeInTheDocument();
  });
});
