"use client";

import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { RefreshCw, ArrowRight, AlertTriangle, ShieldCheck, Send } from 'lucide-react';
import { cn, apiFetch } from '@/lib/utils';
import type { DeliverabilitySummary, EmailLog } from '@/lib/types';

export default function EmailLogsPage() {
  const [logs, setLogs] = useState<EmailLog[]>([]);
  const [summary, setSummary] = useState<DeliverabilitySummary | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const fetchLogs = async () => {
    setIsLoading(true);
    try {
      const res = await apiFetch('/api/email_logs');
      if (res.ok) {
        const data = await res.json();
        setLogs(data);
      }
      const summaryRes = await apiFetch('/api/deliverability/summary');
      if (summaryRes.ok) {
        setSummary(await summaryRes.json());
      }
    } catch (e) {
      console.error(e);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchLogs();
  }, []);

  return (
    <div>
      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:justify-between sm:items-end">
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-indigo-600">Reports</p>
          <h1 className="text-2xl font-semibold tracking-tight text-white sm:text-3xl">Outbound Logs</h1>
          <p className="mt-2 text-sm text-gray-400">History of all messages dispatched by the system.</p>
        </div>
        <Button onClick={fetchLogs} variant="outline" className="gap-2 bg-transparent text-slate-700 border-white/20">
          <RefreshCw className="w-4 h-4" /> Refresh
        </Button>
      </div>

      <div className="grid gap-4 md:grid-cols-3 mb-6">
        <div className="glass-panel rounded-lg border border-white/10 p-4">
          <div className="flex items-center gap-2 text-gray-400 text-sm mb-2">
            <Send className="w-4 h-4" /> Sent
          </div>
          <div className="text-2xl font-bold text-slate-900">{summary?.outbound_count ?? '—'}</div>
        </div>
        <div className="glass-panel rounded-lg border border-white/10 p-4">
          <div className="flex items-center gap-2 text-gray-400 text-sm mb-2">
            <AlertTriangle className="w-4 h-4" /> Failed / Bounced
          </div>
          <div className="text-2xl font-bold text-rose-600">
            {(summary?.status_counts.send_failed || 0) + (summary?.status_counts.bounced || 0) + (summary?.status_counts.invalid_email || 0)}
          </div>
        </div>
        <div className="glass-panel rounded-lg border border-white/10 p-4">
          <div className="flex items-center gap-2 text-gray-400 text-sm mb-2">
            <ShieldCheck className="w-4 h-4" /> Suppressed
          </div>
          <div className="text-2xl font-bold text-amber-700">
            {(summary?.status_counts.needs_email || 0) + (summary?.status_counts.invalid_email || 0)}
          </div>
        </div>
      </div>

      {summary?.risk_domains?.length ? (
        <div className="glass-panel rounded-lg border border-white/10 p-4 mb-6">
          <div className="text-sm font-medium text-gray-300 mb-3">Risk Domains</div>
          <div className="flex flex-wrap gap-2">
            {summary.risk_domains.map(item => (
              <span key={item.domain} className="rounded-full bg-rose-500/10 px-3 py-1 text-xs text-rose-200 border border-rose-500/20">
                {item.domain}: {item.failures} failed
              </span>
            ))}
          </div>
        </div>
      ) : null}

      <div className="glass-panel rounded-lg overflow-hidden border border-white/10">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="bg-white/5 text-gray-400 text-xs uppercase tracking-wider">
              <tr>
                <th className="px-6 py-4 font-semibold">Time</th>
                <th className="px-6 py-4 font-semibold">Direction</th>
                <th className="px-6 py-4 font-semibold">Route</th>
                <th className="px-6 py-4 font-semibold">Lead</th>
                <th className="px-6 py-4 font-semibold">Subject / Info</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5 text-gray-300">
              {isLoading ? (
                <tr>
                  <td colSpan={5} className="px-6 py-12 text-center text-gray-500">Loading logs...</td>
                </tr>
              ) : logs.length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-6 py-12 text-center text-gray-500">No outbound logs yet.</td>
                </tr>
              ) : (
                logs.map(log => (
                  <tr key={log.id} className="hover:bg-white/[0.02] transition-colors">
                    <td className="px-6 py-4 whitespace-nowrap text-gray-400">
                      {log.sent_at ? new Date(log.sent_at).toLocaleString() : '—'}
                    </td>
                    <td className="px-6 py-4">
                      <span className={cn(
                        "px-2 py-1 rounded text-xs font-semibold uppercase",
                        log.direction === 'outbound' ? "bg-indigo-500/10 text-indigo-500" : "bg-emerald-500/10 text-emerald-500"
                      )}>
                        {log.direction}
                      </span>
                    </td>
                    <td className="px-6 py-4">
                      <div className="flex items-center gap-2 text-gray-400">
                        <span>{log.from_email || 'System'}</span>
                        <ArrowRight className="w-3 h-3 text-gray-600" />
                        <span className="text-gray-300">{log.to_email || '—'}</span>
                      </div>
                    </td>
                    <td className="px-6 py-4">
                      <div className="text-gray-300">{log.lead_name || '—'}</div>
                      <div className="text-xs text-gray-500">{log.lead_company || '—'}</div>
                    </td>
                    <td className="px-6 py-4 max-w-xs truncate text-gray-400">
                      {log.subject || log.body || '—'}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
