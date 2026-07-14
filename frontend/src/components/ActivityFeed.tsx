"use client";

import { useEffect, useState } from 'react';
import { Activity, FileText, Send, Search, MessageSquare, XCircle } from 'lucide-react';
import { apiFetch } from '@/lib/utils';
import { useTranslation } from '@/lib/i18n';

interface ActivityItem {
  lead_id: number;
  event: string;
  status: string;
  title: string;
  company?: string | null;
  workflow_id?: number | null;
  at?: string | null;
}

const EVENT_META: Record<string, { icon: typeof Activity; color: string }> = {
  found: { icon: Search, color: 'text-slate-400' },
  drafted: { icon: FileText, color: 'text-indigo-500' },
  sent: { icon: Send, color: 'text-violet-500' },
  replied: { icon: MessageSquare, color: 'text-emerald-500' },
  send_failed: { icon: XCircle, color: 'text-rose-500' },
  rejected: { icon: XCircle, color: 'text-gray-400' },
  unsubscribed: { icon: XCircle, color: 'text-gray-400' },
  updated: { icon: Activity, color: 'text-slate-400' },
};

function relativeTime(iso?: string | null, t?: (k: string) => string): string {
  if (!iso) return '';
  const then = new Date(iso).getTime();
  const diff = Date.now() - then;
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return t ? t('just now') : 'just now';
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h`;
  return `${Math.floor(hrs / 24)}d`;
}

export default function ActivityFeed() {
  const { t } = useTranslation();
  const [items, setItems] = useState<ActivityItem[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const res = await apiFetch('/api/analytics/activity?limit=15');
        if (res.ok && active) setItems((await res.json()).items || []);
      } catch {
        // Non-fatal.
      } finally {
        if (active) setLoading(false);
      }
    };
    load();
    // Refresh periodically so it feels live.
    const timer = window.setInterval(load, 30000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);

  return (
    <div className="glass-panel rounded-lg border border-white/5 p-5">
      <h2 className="mb-4 flex items-center gap-2 text-lg font-semibold text-slate-950">
        <Activity className="h-5 w-5 text-emerald-500" /> {t('Recent Activity')}
      </h2>

      {loading ? (
        <div className="space-y-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="h-6 animate-pulse rounded bg-slate-200/50" />
          ))}
        </div>
      ) : items.length === 0 ? (
        <p className="py-8 text-center text-sm text-gray-400">{t('No recent activity yet.')}</p>
      ) : (
        <ul className="max-h-[320px] space-y-1 overflow-y-auto">
          {items.map((item, i) => {
            const meta = EVENT_META[item.event] || EVENT_META.updated;
            const Icon = meta.icon;
            return (
              <li key={`${item.lead_id}-${i}`} className="flex items-center gap-3 rounded-md px-2 py-2 hover:bg-slate-50">
                <Icon className={`h-4 w-4 shrink-0 ${meta.color}`} />
                <div className="min-w-0 flex-1">
                  <span className="text-sm text-slate-700">
                    <span className="text-slate-500">{t(`activity.${item.event}`)} </span>
                    <span className="font-medium text-slate-900">{item.title}</span>
                  </span>
                  {item.company && item.company !== item.title && (
                    <span className="ml-1 truncate text-xs text-slate-400">· {item.company}</span>
                  )}
                </div>
                <span className="shrink-0 text-xs tabular-nums text-slate-400">{relativeTime(item.at, t)}</span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
