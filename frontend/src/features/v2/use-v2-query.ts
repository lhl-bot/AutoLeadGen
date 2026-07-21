'use client';

import { useCallback, useEffect, useState } from 'react';
import type { DataEnvelope } from './types';

export function useV2Query<T>(loader: (signal?: AbortSignal) => Promise<DataEnvelope<T>>) {
  const [result, setResult] = useState<DataEnvelope<T> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [refreshIndex, setRefreshIndex] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setError(null);
    loader(controller.signal)
      .then(value => setResult(value))
      .catch(reason => {
        if (controller.signal.aborted) return;
        setResult(null);
        setError(reason instanceof Error ? reason : new Error('V2 数据加载失败'));
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [loader, refreshIndex]);

  const refresh = useCallback(() => setRefreshIndex(index => index + 1), []);
  return { result, loading, error, refresh };
}
