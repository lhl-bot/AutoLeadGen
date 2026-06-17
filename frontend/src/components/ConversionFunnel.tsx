"use client";

import { useEffect, useState } from 'react';
import { Filter } from 'lucide-react';
import { apiFetch } from '@/lib/utils';
import { useTranslation } from '@/lib/i18n';

interface Stage { key: string; count: number }
interface FunnelData { stages: Stage[]; reply_rate: number }

const STAGE_COLORS: Record<string, string> = {
  found: 'bg-slate-400',
  with_email: 'bg-sky-500',
  drafted: 'bg-indigo-500',
  sent: 'bg-violet-500',
  replied: 'bg-emerald-500',
};

export default function ConversionFunnel({ workflowId }: { workflowId?: number }) {
  const { t } = useTranslation();
  const [data, setData] = useState<FunnelData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    (async () => {
      setLoading(true);
      try {
        const qs = workflowId ? `?workflow_id=${workflowId}` : '';
        const res = await apiFetch(`/api/analytics/funnel${qs}`);
        if (res.ok && active) setData(await res.json());
      } catch {
        // Non-fatal — the panel just shows its empty state.
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => { active = false; };
  }, [workflowId]);

  const stageLabel = (key: string) => t(`funnel.${key}`);
  const top = data?.stages[0]?.count || 0;

  return (
    <div className="glass-panel rounded-lg border border-white/5 p-5">
      <h2 className="mb-4 flex items-center gap-2 text-lg font-semibold text-slate-950">
        <Filter className="h-5 w-5 text-indigo-500" /> {t('Conversion Funnel')}
      </h2>

      {loading ? (
        <div className="space-y-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="h-7 animate-pulse rounded bg-slate-200/50" />
          ))}
        </div>
      ) : !data || top === 0 ? (
        <p className="py-8 text-center text-sm text-gray-400">{t('No funnel data yet — activate a workflow to start.')}</p>
      ) : (
        <div className="space-y-2.5">
          {data.stages.map((stage) => {
            const pct = top > 0 ? Math.round((stage.count / top) * 100) : 0;
            const conv = top > 0 ? Math.round((stage.count / top) * 100) : 0;
            return (
              <div key={stage.key}>
                <div className="mb-1 flex items-center justify-between text-xs">
                  <span className="font-medium text-slate-600">{stageLabel(stage.key)}</span>
                  <span className="tabular-nums text-slate-500">{stage.count} · {conv}%</span>
                </div>
                <div className="h-6 w-full overflow-hidden rounded bg-slate-100">
                  <div
                    className={`h-full rounded ${STAGE_COLORS[stage.key] || 'bg-slate-400'} transition-all`}
                    style={{ width: `${Math.max(pct, stage.count > 0 ? 4 : 0)}%` }}
                  />
                </div>
              </div>
            );
          })}
          <div className="mt-3 border-t border-slate-200/70 pt-3 text-xs text-slate-500">
            {t('Reply rate (of sent)')}: <span className="font-semibold text-emerald-600">{Math.round((data.reply_rate || 0) * 100)}%</span>
          </div>
        </div>
      )}
    </div>
  );
}
