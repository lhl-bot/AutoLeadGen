"use client";

import { useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { AlertTriangle, RefreshCw } from 'lucide-react';
import { useTranslation } from '@/lib/i18n';

export default function DashboardError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const { t } = useTranslation();

  useEffect(() => {
    console.error('Dashboard error boundary caught:', error);
  }, [error]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-100 p-6">
      <div className="w-full max-w-md rounded-lg border border-red-200 bg-white p-10 text-center shadow-xl shadow-slate-950/10">
        <div className="w-12 h-12 rounded-full bg-red-500/10 flex items-center justify-center mx-auto mb-6">
          <AlertTriangle className="w-6 h-6 text-red-400" />
        </div>
        <h2 className="mb-2 text-xl font-semibold text-slate-950">{t('Error') || 'Something went wrong'}</h2>
        <p className="mb-6 text-sm text-slate-500">
          {error.message || 'An unexpected error occurred while loading this page.'}
        </p>
        <Button
          onClick={reset}
          className="gap-2 bg-indigo-600 hover:bg-indigo-700 text-white"
        >
          <RefreshCw className="w-4 h-4" /> {t('Refresh')}
        </Button>
      </div>
    </div>
  );
}
