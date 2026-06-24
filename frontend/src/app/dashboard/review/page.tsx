"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  AlertTriangle,
  CheckCheck,
  Inbox,
  Keyboard,
  Loader2,
  MailCheck,
  MailQuestion,
  Pencil,
  RefreshCw,
  RotateCcw,
  Send,
  SkipForward,
  Sparkles,
  ThumbsDown,
} from 'lucide-react';
import { cn, apiFetch, formatApiDetail } from '@/lib/utils';
import type {
  BulkLeadResponse,
  Lead,
  ReviewCenterData,
  ReviewQueueKey,
} from '@/lib/types';
import { useTranslation } from '@/lib/i18n';
import { toast } from 'sonner';

const QUEUES: Array<{
  key: ReviewQueueKey
  label: string
  description: string
  icon: typeof MailCheck
}> = [
  {
    key: 'drafted',
    label: 'Drafts to review',
    description: 'Review, edit, approve, or reject AI drafts.',
    icon: MailCheck,
  },
  {
    key: 'needs_email',
    label: 'Missing email',
    description: 'Leads that need enrichment before outreach.',
    icon: MailQuestion,
  },
  {
    key: 'send_failed',
    label: 'Send failures',
    description: 'Failed sends that can be returned to the review queue.',
    icon: AlertTriangle,
  },
  {
    key: 'high_intent',
    label: 'High intent',
    description: 'Replies and handoff signals that need human attention.',
    icon: Sparkles,
  },
];

function gradeClasses(grade?: string | null): string {
  switch ((grade || '').toUpperCase()) {
    case 'A': return 'bg-emerald-500/10 text-emerald-600 border-emerald-500/20';
    case 'B': return 'bg-sky-500/10 text-sky-600 border-sky-500/20';
    case 'C': return 'bg-amber-500/10 text-amber-600 border-amber-500/20';
    default: return 'bg-slate-500/10 text-slate-500 border-slate-500/20';
  }
}

function leadTitle(lead: Lead): string {
  const name = `${lead.first_name || ''} ${lead.last_name || ''}`.trim();
  return name || lead.company_name || lead.email || lead.domain || `#${lead.id}`;
}

export default function ReviewQueuePage() {
  const { t } = useTranslation();
  const [center, setCenter] = useState<ReviewCenterData | null>(null);
  const [activeQueue, setActiveQueue] = useState<ReviewQueueKey>('drafted');
  const [isLoading, setIsLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [checkedIds, setCheckedIds] = useState<number[]>([]);
  const [draft, setDraft] = useState('');
  const [sendingId, setSendingId] = useState<number | null>(null);
  const [bulkAction, setBulkAction] = useState<'send' | 'reject' | 'retry' | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const leads = useMemo(
    () => center?.queues[activeQueue] || [],
    [activeQueue, center],
  );
  const selected = leads.find(lead => lead.id === selectedId) || null;

  const fetchCenter = useCallback(async () => {
    setIsLoading(true);
    try {
      const res = await apiFetch('/api/leads/review-center?limit=200');
      if (!res.ok) {
        const error = await res.json().catch(() => ({}));
        toast.error(formatApiDetail(error.detail, t('Failed to load review queue')));
        return;
      }
      const data: ReviewCenterData = await res.json();
      setCenter(data);
      setSelectedId(current => {
        const queue = data.queues[activeQueue];
        return current && queue.some(lead => lead.id === current)
          ? current
          : (queue[0]?.id ?? null);
      });
      setCheckedIds(current => current.filter(id => data.queues[activeQueue].some(lead => lead.id === id)));
    } catch {
      toast.error(t('Network error loading review queue'));
    } finally {
      setIsLoading(false);
    }
  }, [activeQueue, t]);

  useEffect(() => {
    fetchCenter();
  }, [fetchCenter]);

  useEffect(() => {
    setSelectedId(leads[0]?.id ?? null);
    setCheckedIds([]);
  }, [activeQueue]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    setDraft(selected?.ai_draft || '');
  }, [selectedId, selected?.ai_draft]);

  const selectByOffset = useCallback((offset: number) => {
    setSelectedId(current => {
      const index = leads.findIndex(lead => lead.id === current);
      if (index === -1) return leads[0]?.id ?? null;
      const next = Math.min(leads.length - 1, Math.max(0, index + offset));
      return leads[next]?.id ?? current;
    });
  }, [leads]);

  const sendDraft = useCallback(async (lead: Lead | null, draftText: string) => {
    if (!lead) return;
    if (!draftText.trim()) {
      toast.error(t('Draft is empty'));
      return;
    }
    setSendingId(lead.id);
    try {
      const res = await apiFetch(`/api/leads/${lead.id}/send-draft`, {
        method: 'POST',
        body: JSON.stringify({ draft: draftText }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.ok) {
        toast.success(`${t('Sent to')} ${leadTitle(lead)}`);
        await fetchCenter();
      } else {
        toast.error(formatApiDetail(data.detail, data.message || t('Send was not completed')));
      }
    } catch {
      toast.error(t('Network error sending draft'));
    } finally {
      setSendingId(null);
    }
  }, [fetchCenter, t]);

  const runBulk = useCallback(async (action: 'send' | 'reject' | 'retry') => {
    if (!checkedIds.length) return;
    setBulkAction(action);
    try {
      const endpoint = action === 'send' ? '/api/leads/bulk/send-drafts' : '/api/leads/bulk/action';
      const payload = action === 'send'
        ? { lead_ids: checkedIds }
        : { lead_ids: checkedIds, action };
      const res = await apiFetch(endpoint, {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      const data: BulkLeadResponse = await res.json();
      if (!res.ok) {
        toast.error(formatApiDetail(data, t('Bulk action failed')));
        return;
      }
      if (data.failed) {
        const firstFailure = data.results.find(result => !result.ok);
        toast.warning(
          `${data.succeeded}/${data.requested} ${t('completed')}. ${firstFailure?.message || ''}`,
        );
      } else {
        toast.success(`${data.succeeded} ${t('items completed')}`);
      }
      setCheckedIds([]);
      await fetchCenter();
    } catch {
      toast.error(t('Bulk action failed'));
    } finally {
      setBulkAction(null);
    }
  }, [checkedIds, fetchCenter, t]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const inEditor = document.activeElement === textareaRef.current;
      if ((event.metaKey || event.ctrlKey) && event.key === 'Enter' && activeQueue === 'drafted') {
        event.preventDefault();
        sendDraft(selected, draft);
        return;
      }
      if (inEditor) return;
      if (event.key === 'j') {
        event.preventDefault();
        selectByOffset(1);
      } else if (event.key === 'k') {
        event.preventDefault();
        selectByOffset(-1);
      } else if (event.key === 'e' && activeQueue === 'drafted') {
        event.preventDefault();
        textareaRef.current?.focus();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [activeQueue, draft, selectByOffset, selected, sendDraft]);

  const toggleChecked = (leadId: number) => {
    setCheckedIds(current => (
      current.includes(leadId)
        ? current.filter(id => id !== leadId)
        : [...current, leadId]
    ));
  };

  const allChecked = leads.length > 0 && leads.every(lead => checkedIds.includes(lead.id));
  const activeConfig = QUEUES.find(queue => queue.key === activeQueue) || QUEUES[0];
  const ActiveQueueIcon = activeConfig.icon;

  return (
    <div>
      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-indigo-600">{t('Workspace')}</p>
          <h1 className="text-2xl font-semibold tracking-tight text-white sm:text-3xl">{t('Review Center')}</h1>
          <p className="mt-2 text-sm text-gray-400">{t('Handle drafts, delivery problems, enrichment gaps, and high-intent replies in one place.')}</p>
        </div>
        <div className="flex items-center gap-3">
          <span className="hidden items-center gap-1.5 rounded-md border border-white/10 px-2.5 py-1 text-xs text-gray-400 sm:inline-flex">
            <Keyboard className="h-3.5 w-3.5" /> J/K · E · ⌘↵
          </span>
          <Button onClick={fetchCenter} variant="outline" className="gap-2 bg-transparent text-slate-700 border-white/20">
            <RefreshCw className={cn('h-4 w-4', isLoading && 'animate-spin')} /> {t('Refresh')}
          </Button>
        </div>
      </div>

      <div className="mb-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {QUEUES.map(queue => {
          const Icon = queue.icon;
          const count = center?.counts[queue.key] || 0;
          const isActive = activeQueue === queue.key;
          return (
            <button
              type="button"
              key={queue.key}
              onClick={() => setActiveQueue(queue.key)}
              className={cn(
                'glass-panel rounded-lg border p-4 text-left transition-all',
                isActive
                  ? 'border-indigo-500/50 ring-1 ring-indigo-500/20'
                  : 'border-white/10 hover:border-white/20',
              )}
            >
              <div className="flex items-center justify-between">
                <Icon className={cn('h-5 w-5', isActive ? 'text-indigo-500' : 'text-gray-400')} />
                <span className="text-2xl font-semibold text-white">{count}</span>
              </div>
              <p className="mt-3 text-sm font-semibold text-white">{t(queue.label)}</p>
              <p className="mt-1 text-xs leading-relaxed text-gray-400">{t(queue.description)}</p>
            </button>
          );
        })}
      </div>

      {isLoading && !center ? (
        <div className="glass-panel flex items-center gap-2 rounded-lg p-12 text-gray-400">
          <Loader2 className="h-5 w-5 animate-spin" /> {t('Loading...')}
        </div>
      ) : (
        <div className="grid gap-4 lg:grid-cols-[360px_1fr]">
          <div className="glass-panel max-h-[72vh] overflow-y-auto rounded-lg p-2">
            <div className="sticky top-0 z-10 mb-2 rounded-md bg-slate-950/90 px-2 py-2 backdrop-blur">
              <div className="flex items-center justify-between gap-2">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wide text-gray-300">
                    {t(activeConfig.label)}
                  </p>
                  <p className="mt-0.5 text-[11px] text-gray-500">{leads.length} {t('items loaded')}</p>
                </div>
                {leads.length > 0 && (
                  <label className="flex cursor-pointer items-center gap-2 text-xs text-gray-400">
                    <input
                      type="checkbox"
                      checked={allChecked}
                      onChange={() => setCheckedIds(allChecked ? [] : leads.map(lead => lead.id))}
                      className="h-4 w-4 rounded border-white/20"
                    />
                    {t('Select all')}
                  </label>
                )}
              </div>

              {checkedIds.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-2 border-t border-white/10 pt-3">
                  {activeQueue === 'drafted' && (
                    <>
                      <Button size="sm" onClick={() => runBulk('send')} disabled={bulkAction !== null} className="gap-1.5">
                        {bulkAction === 'send' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CheckCheck className="h-3.5 w-3.5" />}
                        {t('Send selected')}
                      </Button>
                      <Button size="sm" variant="outline" onClick={() => runBulk('reject')} disabled={bulkAction !== null} className="gap-1.5">
                        <ThumbsDown className="h-3.5 w-3.5" /> {t('Reject selected')}
                      </Button>
                    </>
                  )}
                  {activeQueue === 'send_failed' && (
                    <Button size="sm" onClick={() => runBulk('retry')} disabled={bulkAction !== null} className="gap-1.5">
                      {bulkAction === 'retry' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RotateCcw className="h-3.5 w-3.5" />}
                      {t('Return to review')}
                    </Button>
                  )}
                </div>
              )}
            </div>

            {leads.length === 0 ? (
              <div className="px-4 py-16 text-center text-gray-400">
                <Inbox className="mx-auto mb-3 h-10 w-10 opacity-40" />
                <p className="text-sm">{t('No items in this queue')}</p>
              </div>
            ) : leads.map(lead => (
              <div
                key={lead.id}
                className={cn(
                  'mb-1 flex items-start gap-2 rounded-md px-2 py-2 transition-colors',
                  lead.id === selectedId ? 'bg-indigo-500/15' : 'hover:bg-white/5',
                )}
              >
                <input
                  type="checkbox"
                  checked={checkedIds.includes(lead.id)}
                  onChange={() => toggleChecked(lead.id)}
                  aria-label={`${t('Select')} ${leadTitle(lead)}`}
                  className="mt-1 h-4 w-4 rounded border-white/20"
                />
                <button
                  type="button"
                  onClick={() => setSelectedId(lead.id)}
                  className="min-w-0 flex-1 text-left"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate text-sm font-medium text-white">{leadTitle(lead)}</span>
                    {lead.fit_grade && (
                      <Badge variant="secondary" className={cn('shrink-0 border text-[10px]', gradeClasses(lead.fit_grade))}>
                        {lead.fit_grade}
                      </Badge>
                    )}
                  </div>
                  <div className="truncate text-xs text-gray-400">{lead.company_name || lead.domain}</div>
                  {lead.reply_snippet && (
                    <div className="mt-1 line-clamp-2 text-[11px] text-indigo-300">&ldquo;{lead.reply_snippet}&rdquo;</div>
                  )}
                </button>
              </div>
            ))}
          </div>

          {selected ? (
            <div className="glass-panel rounded-lg p-5">
              <div className="mb-4 flex flex-wrap items-start justify-between gap-3 border-b border-white/10 pb-4">
                <div className="min-w-0">
                  <h2 className="flex flex-wrap items-center gap-2 text-lg font-semibold text-white">
                    {leadTitle(selected)}
                    {selected.fit_grade && (
                      <Badge variant="secondary" className={cn('border', gradeClasses(selected.fit_grade))}>
                        {t('Fit')} {selected.fit_grade} · {selected.fit_score ?? '—'}
                      </Badge>
                    )}
                    {selected.handoff_recommended && (
                      <Badge className="border border-amber-500/20 bg-amber-500/10 text-amber-500">
                        {t('Human handoff')}
                      </Badge>
                    )}
                  </h2>
                  <div className="mt-1 flex flex-wrap items-center gap-x-3 text-xs text-gray-400">
                    <span className="inline-flex items-center gap-1">
                      {selected.email_verified ? <MailCheck className="h-3 w-3 text-emerald-500" /> : null}
                      {selected.email || t('No email')}
                    </span>
                    {selected.job_title && <span>· {selected.job_title}</span>}
                    {selected.company_name && <span>· {selected.company_name}</span>}
                    <span>· {t('Status')}: {selected.status}</span>
                  </div>
                </div>
              </div>

              {selected.qualification_notes && (
                <div className="mb-4 rounded-lg border border-sky-500/20 bg-sky-500/5 p-3">
                  <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-sky-500">{t('Why this score')}</p>
                  <p className="text-sm text-gray-300">{selected.qualification_notes}</p>
                </div>
              )}

              {selected.reply_snippet && (
                <div className="mb-4 rounded-lg border border-indigo-500/20 bg-indigo-500/5 p-4">
                  <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-indigo-400">{t('Latest reply')}</p>
                  <p className="text-sm leading-relaxed text-gray-200">{selected.reply_snippet}</p>
                </div>
              )}

              {activeQueue === 'drafted' ? (
                <>
                  <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-gray-400">
                    <Pencil className="h-3.5 w-3.5" /> {t('AI draft')}
                  </div>
                  <textarea
                    ref={textareaRef}
                    value={draft}
                    onChange={event => setDraft(event.target.value)}
                    className="min-h-[280px] w-full resize-y rounded-lg border border-white/10 bg-black/40 p-4 text-sm leading-relaxed text-gray-200 focus:border-indigo-500/50 focus:outline-none"
                  />
                  <div className="mt-4 flex items-center gap-3">
                    <Button
                      onClick={() => sendDraft(selected, draft)}
                      disabled={sendingId === selected.id || !selected.email}
                      className="gap-2"
                      title={!selected.email ? t('No email') : undefined}
                    >
                      {sendingId === selected.id
                        ? <><Loader2 className="h-4 w-4 animate-spin" /> {t('Sending...')}</>
                        : <><Send className="h-4 w-4" /> {t('Approve & Send')}</>}
                    </Button>
                    <Button variant="ghost" onClick={() => selectByOffset(1)} className="gap-2 text-gray-500">
                      <SkipForward className="h-4 w-4" /> {t('Skip')}
                    </Button>
                    <span className="ml-auto text-xs text-gray-400">⌘↵ {t('to send')}</span>
                  </div>
                </>
              ) : (
                <div className="rounded-lg border border-dashed border-white/10 p-8 text-center text-gray-400">
                  <ActiveQueueIcon className="mx-auto mb-3 h-8 w-8 opacity-60" />
                  <p className="font-medium text-white">{t(activeConfig.label)}</p>
                  <p className="mt-1 text-sm">{t(activeConfig.description)}</p>
                </div>
              )}
            </div>
          ) : (
            <div className="glass-panel flex items-center justify-center rounded-lg p-12 text-gray-400">
              {t('Select an item to review')}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
