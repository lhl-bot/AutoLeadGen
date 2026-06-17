"use client";

import { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  MailCheck, RefreshCw, Send, SkipForward, Loader2, Inbox, Keyboard, Pencil,
} from 'lucide-react';
import { cn, apiFetch, formatApiDetail } from '@/lib/utils';
import type { Lead } from '@/lib/types';
import { useTranslation } from '@/lib/i18n';
import { toast } from 'sonner';

function gradeClasses(grade?: string | null): string {
  switch ((grade || '').toUpperCase()) {
    case 'A': return 'bg-emerald-500/10 text-emerald-500 border-emerald-500/20';
    case 'B': return 'bg-sky-500/10 text-sky-400 border-sky-500/20';
    case 'C': return 'bg-amber-500/10 text-amber-500 border-amber-500/20';
    default: return 'bg-gray-500/10 text-gray-400 border-gray-500/20';
  }
}

function leadTitle(lead: Lead): string {
  const name = `${lead.first_name || ''} ${lead.last_name || ''}`.trim();
  return name || lead.company_name || lead.email || lead.domain || `#${lead.id}`;
}

export default function ReviewQueuePage() {
  const { t } = useTranslation();
  const [leads, setLeads] = useState<Lead[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [draft, setDraft] = useState('');
  const [sendingId, setSendingId] = useState<number | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const selected = leads.find(l => l.id === selectedId) || null;

  const fetchQueue = useCallback(async () => {
    setIsLoading(true);
    try {
      const res = await apiFetch('/api/leads?status=drafted&limit=200');
      if (res.ok) {
        const data: Lead[] = await res.json();
        // Highest-fit drafts first so the best leads get reviewed first.
        data.sort((a, b) => (b.fit_score ?? -1) - (a.fit_score ?? -1));
        setLeads(data);
        setSelectedId(prev => (prev && data.some(l => l.id === prev)) ? prev : (data[0]?.id ?? null));
      } else {
        const err = await res.json().catch(() => ({}));
        toast.error(formatApiDetail(err.detail, t('Failed to load review queue')));
      }
    } catch {
      toast.error(t('Network error loading review queue'));
    } finally {
      setIsLoading(false);
    }
  }, [t]);

  useEffect(() => { fetchQueue(); }, [fetchQueue]);

  // Load the selected lead's draft into the editor.
  useEffect(() => {
    setDraft(selected?.ai_draft || '');
  }, [selectedId, selected?.ai_draft]);

  const selectByOffset = useCallback((offset: number) => {
    setSelectedId(curr => {
      const idx = leads.findIndex(l => l.id === curr);
      if (idx === -1) return leads[0]?.id ?? null;
      const next = Math.min(leads.length - 1, Math.max(0, idx + offset));
      return leads[next]?.id ?? curr;
    });
  }, [leads]);

  const sendDraft = useCallback(async (lead: Lead | null, draftText: string) => {
    if (!lead) return;
    if (!draftText.trim()) { toast.error(t('Draft is empty')); return; }
    setSendingId(lead.id);
    try {
      const res = await apiFetch(`/api/leads/${lead.id}/send-draft`, {
        method: 'POST',
        body: JSON.stringify({ draft: draftText }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.ok) {
        toast.success(`${t('Sent to')} ${leadTitle(lead)}`);
        // Drop it from the queue and advance to the neighbour.
        setLeads(prev => {
          const idx = prev.findIndex(l => l.id === lead.id);
          const next = prev.filter(l => l.id !== lead.id);
          setSelectedId(next[Math.min(idx, next.length - 1)]?.id ?? null);
          return next;
        });
      } else {
        toast.error(formatApiDetail(data.detail, data.message || t('Send was not completed')));
      }
    } catch {
      toast.error(t('Network error sending draft'));
    } finally {
      setSendingId(null);
    }
  }, [t]);

  // Keyboard shortcuts: J/K navigate, E edit, Cmd/Ctrl+Enter send.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const inEditor = document.activeElement === textareaRef.current;
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
        e.preventDefault();
        sendDraft(selected, draft);
        return;
      }
      if (inEditor) return; // Don't hijack typing.
      if (e.key === 'j') { e.preventDefault(); selectByOffset(1); }
      else if (e.key === 'k') { e.preventDefault(); selectByOffset(-1); }
      else if (e.key === 'e') { e.preventDefault(); textareaRef.current?.focus(); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [selected, draft, sendDraft, selectByOffset]);

  return (
    <div>
      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:justify-between sm:items-end">
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-indigo-600">{t('Workspace')}</p>
          <h1 className="text-2xl font-semibold tracking-tight text-white sm:text-3xl">{t('Review Queue')}</h1>
          <p className="mt-2 text-sm text-gray-400">{t('Review AI drafts and send the ones you approve.')}</p>
        </div>
        <div className="flex items-center gap-3">
          <span className="hidden items-center gap-1.5 rounded-md border border-white/10 px-2.5 py-1 text-xs text-gray-400 sm:inline-flex">
            <Keyboard className="h-3.5 w-3.5" /> J/K · E · ⌘↵
          </span>
          <Button onClick={fetchQueue} variant="outline" className="gap-2 bg-transparent text-slate-700 border-white/20">
            <RefreshCw className="w-4 h-4" /> {t('Refresh')}
          </Button>
        </div>
      </div>

      {isLoading ? (
        <div className="glass-panel flex items-center gap-2 rounded-lg p-12 text-gray-400">
          <Loader2 className="h-5 w-5 animate-spin" /> {t('Loading...')}
        </div>
      ) : leads.length === 0 ? (
        <div className="glass-panel rounded-lg border border-dashed border-white/20 p-12 text-center text-gray-400">
          <Inbox className="mx-auto mb-4 h-12 w-12 opacity-50" />
          <p className="mb-1 font-medium text-white">{t('Inbox zero — no drafts to review')}</p>
          <p className="text-sm">{t('New AI drafts will appear here as the engine writes them.')}</p>
        </div>
      ) : (
        <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
          {/* Master list */}
          <div className="glass-panel max-h-[70vh] overflow-y-auto rounded-lg p-2">
            <div className="px-2 py-1.5 text-xs font-semibold uppercase tracking-wide text-gray-400">
              {leads.length} {t('drafts pending')}
            </div>
            {leads.map(lead => (
              <button
                key={lead.id}
                onClick={() => setSelectedId(lead.id)}
                className={cn(
                  'mb-1 w-full rounded-md px-3 py-2.5 text-left transition-colors',
                  lead.id === selectedId ? 'bg-indigo-500/15' : 'hover:bg-white/5'
                )}
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
              </button>
            ))}
          </div>

          {/* Detail / editor */}
          {selected ? (
            <div className="glass-panel rounded-lg p-5">
              <div className="mb-4 flex flex-wrap items-start justify-between gap-3 border-b border-white/10 pb-4">
                <div className="min-w-0">
                  <h2 className="flex items-center gap-2 text-lg font-semibold text-white">
                    {leadTitle(selected)}
                    {selected.fit_grade && (
                      <Badge variant="secondary" className={cn('border', gradeClasses(selected.fit_grade))}>
                        {t('Fit')} {selected.fit_grade} · {selected.fit_score ?? '—'}
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
                  </div>
                </div>
              </div>

              {selected.qualification_notes && (
                <div className="mb-4 rounded-lg border border-sky-500/20 bg-sky-500/5 p-3">
                  <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-sky-500">{t('Why this score')}</p>
                  <p className="text-sm text-gray-300">{selected.qualification_notes}</p>
                </div>
              )}

              <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-gray-400">
                <Pencil className="h-3.5 w-3.5" /> {t('AI draft')}
              </div>
              <textarea
                ref={textareaRef}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                className="w-full min-h-[280px] resize-y rounded-lg border border-white/10 bg-black/40 p-4 text-sm leading-relaxed text-gray-200 focus:border-indigo-500/50 focus:outline-none"
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
            </div>
          ) : (
            <div className="glass-panel flex items-center justify-center rounded-lg p-12 text-gray-400">
              {t('Select a draft to review')}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
