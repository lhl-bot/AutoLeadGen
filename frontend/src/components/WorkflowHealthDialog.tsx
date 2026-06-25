"use client";

import { useCallback, useEffect, useState } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { apiFetch } from '@/lib/utils';
import { useTranslation } from '@/lib/i18n';
import { Activity, AlertTriangle, CheckCircle2, XCircle, RefreshCw, Search, FileText, Send, MessageSquare } from 'lucide-react';
import type { WorkflowHealth } from '@/lib/types';

const FUNNEL_STEPS: Array<{ key: keyof WorkflowHealth['funnel']; en: string; zh: string; icon: typeof Search; color: string }> = [
  { key: 'found', en: 'Found', zh: '已发现', icon: Search, color: 'text-slate-600' },
  { key: 'drafted', en: 'Drafted', zh: '已起草', icon: FileText, color: 'text-indigo-600' },
  { key: 'sent', en: 'Sent', zh: '已发送', icon: Send, color: 'text-blue-600' },
  { key: 'replied', en: 'Replied', zh: '已回复', icon: MessageSquare, color: 'text-emerald-600' },
];

const PROVIDER_LABELS: Record<string, string> = {
  leadcontact: 'LeadContact', snovio: 'Snov.io', tavily: 'Tavily', bocha: 'Bocha',
};

export default function WorkflowHealthDialog({ workflowId, onClose }: { workflowId: number | null; onClose: () => void }) {
  const { language } = useTranslation();
  const txt = (en: string, zh: string) => (language === 'zh' ? zh : en);
  const [data, setData] = useState<WorkflowHealth | null>(null);
  const [loading, setLoading] = useState(false);

  const fetchHealth = useCallback(async (id: number) => {
    setLoading(true);
    try {
      const res = await apiFetch(`/api/workflows/${id}/health`);
      if (res.ok) setData(await res.json());
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (workflowId != null) { setData(null); fetchHealth(workflowId); }
  }, [workflowId, fetchHealth]);

  const open = workflowId != null;
  const maxFunnel = data ? Math.max(1, ...FUNNEL_STEPS.map(s => data.funnel[s.key])) : 1;

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="sm:max-w-2xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Activity className="h-5 w-5 text-indigo-500" />
            {txt('Workflow Health', '工作流健康')}
            {data && <span className="text-sm font-normal text-slate-500">· {data.workflow.name}</span>}
          </DialogTitle>
        </DialogHeader>

        {loading && !data ? (
          <div className="py-16 text-center text-slate-500">{txt('Loading...', '加载中...')}</div>
        ) : data ? (
          <div className="space-y-5">
            {/* Warnings */}
            {data.warnings.length > 0 && (
              <div className="space-y-2">
                {data.warnings.map((w, i) => (
                  <div key={i} className="flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /> {w}
                  </div>
                ))}
              </div>
            )}

            {/* Totals */}
            <div className="grid grid-cols-3 gap-3">
              {[
                { label: txt('Total leads', '线索总数'), value: data.totals.total_leads },
                { label: txt('With email', '有邮箱'), value: data.totals.with_email },
                { label: txt('Sent 24h', '24h 已发'), value: data.recent.emails_sent_24h },
              ].map((s, i) => (
                <div key={i} className="rounded-lg border border-slate-200 bg-slate-50/60 p-3 text-center">
                  <div className="text-2xl font-semibold text-slate-900">{s.value}</div>
                  <div className="text-xs text-slate-500">{s.label}</div>
                </div>
              ))}
            </div>

            {/* Funnel */}
            <div>
              <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">{txt('Funnel', '漏斗')}</div>
              <div className="space-y-1.5">
                {FUNNEL_STEPS.map(step => {
                  const Icon = step.icon;
                  const val = data.funnel[step.key];
                  return (
                    <div key={step.key} className="flex items-center gap-3">
                      <div className="flex w-20 items-center gap-1.5 text-sm text-slate-600">
                        <Icon className={`h-3.5 w-3.5 ${step.color}`} /> {txt(step.en, step.zh)}
                      </div>
                      <div className="h-5 flex-1 overflow-hidden rounded bg-slate-100">
                        <div className="h-full rounded bg-indigo-400" style={{ width: `${(val / maxFunnel) * 100}%` }} />
                      </div>
                      <span className="w-10 text-right text-sm font-medium text-slate-700">{val}</span>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Stuck */}
            {data.stuck.length > 0 && (
              <div>
                <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">{txt('Stuck / parked', '卡住 / 已归档')}</div>
                <div className="space-y-1.5">
                  {data.stuck.map(s => (
                    <div key={s.status} className="flex items-center justify-between rounded-md border border-slate-200 px-3 py-2 text-sm">
                      <span className="text-slate-600">{s.reason}</span>
                      <span className="rounded bg-rose-50 px-2 py-0.5 font-medium text-rose-600">{s.count}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Providers */}
            <div>
              <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">{txt('Providers & config', '依赖与配置')}</div>
              <div className="flex flex-wrap gap-2">
                {Object.entries(PROVIDER_LABELS).map(([key, label]) => {
                  const ok = (data.providers as Record<string, unknown>)[key] === 'configured';
                  return (
                    <span key={key} className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-medium ${ok ? 'bg-emerald-50 text-emerald-700' : 'bg-rose-50 text-rose-700'}`}>
                      {ok ? <CheckCircle2 className="h-3.5 w-3.5" /> : <XCircle className="h-3.5 w-3.5" />} {label}
                    </span>
                  );
                })}
                <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-medium ${data.providers.sender_accounts > 0 ? 'bg-emerald-50 text-emerald-700' : 'bg-rose-50 text-rose-700'}`}>
                  {data.providers.sender_accounts > 0 ? <CheckCircle2 className="h-3.5 w-3.5" /> : <XCircle className="h-3.5 w-3.5" />} {txt('Sender', '发信邮箱')} ({data.providers.sender_accounts})
                </span>
              </div>
              <div className="mt-2 flex flex-wrap gap-2 text-xs">
                <span className={`rounded px-2 py-0.5 ${data.providers.auto_send_drafts ? 'bg-blue-50 text-blue-700' : 'bg-slate-100 text-slate-500'}`}>
                  {txt('Auto-send', '自动发送')}: {data.providers.auto_send_drafts ? txt('on', '开') : txt('review mode', '审核模式')}
                </span>
                <span className={`rounded px-2 py-0.5 ${data.providers.email_require_verified ? 'bg-slate-100 text-slate-600' : 'bg-amber-50 text-amber-700'}`}>
                  {txt('Require verified email', '要求验证邮箱')}: {data.providers.email_require_verified ? txt('yes', '是') : txt('no', '否')}
                </span>
              </div>
            </div>

            <div className="flex items-center justify-between border-t border-slate-100 pt-3 text-xs text-slate-400">
              <span>{txt('New leads 24h', '24h 新增')}: {data.recent.leads_24h}</span>
              <button onClick={() => workflowId != null && fetchHealth(workflowId)} className="inline-flex items-center gap-1 text-slate-500 hover:text-indigo-600">
                <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} /> {txt('Refresh', '刷新')}
              </button>
            </div>
          </div>
        ) : (
          <div className="py-16 text-center text-slate-400">{txt('No data', '暂无数据')}</div>
        )}
      </DialogContent>
    </Dialog>
  );
}
