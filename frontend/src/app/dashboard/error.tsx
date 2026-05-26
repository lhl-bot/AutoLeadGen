"use client";

import { useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { AlertTriangle, RefreshCw } from 'lucide-react';

export default function DashboardError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error('Dashboard error boundary caught:', error);
  }, [error]);

  return (
    <div className="min-h-screen bg-slate-950 flex items-center justify-center p-6">
      <div className="glass-panel p-10 rounded-lg border border-red-500/20 max-w-md w-full text-center">
        <div className="w-12 h-12 rounded-full bg-red-500/10 flex items-center justify-center mx-auto mb-6">
          <AlertTriangle className="w-6 h-6 text-red-400" />
        </div>
        <h2 className="text-xl font-semibold text-white mb-2">Something went wrong</h2>
        <p className="text-sm text-gray-400 mb-6">
          {error.message || 'An unexpected error occurred while loading this page.'}
        </p>
        <Button
          onClick={reset}
          className="gap-2 bg-indigo-600 hover:bg-indigo-700 text-white"
        >
          <RefreshCw className="w-4 h-4" /> Try Again
        </Button>
      </div>
    </div>
  );
}
