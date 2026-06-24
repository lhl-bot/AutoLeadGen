"use client";

import { useCallback, useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import {
  Database, RefreshCw, Search, ThumbsUp, ThumbsDown, FileText,
  ChevronLeft, ChevronRight, Mail, MailCheck, ExternalLink, Loader2, X,
} from 'lucide-react';
import { cn, apiFetch, formatApiDetail } from '@/lib/utils';
import type { Lead, Workflow, LeadBrief } from '@/lib/types';
import { useTranslation } from '@/lib/i18n';
import { toast } from 'sonner';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';

const PAGE_SIZE = 50;

const STATUS_OPTIONS = [
  'found', 'needs_email', 'drafted', 'sent', 'send_failed', 'replied', 'rejected', 'unsubscribed',
];

function statusClasses(status: string): string {
  switch (status) {
    case 'replied': return 'bg-emerald-500/20 text-emerald-500';
    case 'sent': return 'bg-sky-500/20 text-sky-500';
    case 'drafted': return 'bg-indigo-500/20 text-indigo-500';
    case 'send_failed': return 'bg-rose-500/20 text-rose-500';
    case 'rejected':
    case 'unsubscribed': return 'bg-gray-500/20 text-slate-500';
    case 'needs_email': return 'bg-amber-500/20 text-amber-600';
    default: return 'bg-gray-500/20 text-slate-500';
  }
}

function gradeClasses(grade?: string | null): string {
  switch ((grade || '').toUpperCase()) {
    case 'A': return 'bg-emerald-500/10 text-emerald-500 border-emerald-500/20';
    case 'B': return 'bg-sky-500/10 text-sky-400 border-sky-500/20';
    case 'C': return 'bg-amber-500/10 text-amber-500 border-amber-500/20';
    default: return 'bg-gray-500/10 text-slate-500 border-gray-500/20';
  }
}

export default function LeadsPage() {
  const { t } = useTranslation();
  const [leads, setLeads] = useState<Lead[]>([]);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [page, setPage] = useState(0);
  const [hasMore, setHasMore] = useState(false);

  // Filters
  const [search, setSearch] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [status, setStatus] = useState('');
  const [workflowId, setWorkflowId] = useState<string>('');

  const [ratingId, setRatingId] = useState<number | null>(null);
  const [detailLead, setDetailLead] = useState<Lead | null>(null);
  const [brief, setBrief] = useState<LeadBrief | null>(null);
  const [briefLoading, setBriefLoading] = useState(false);

  const fetchWorkflows = useCallback(async () => {
    try {
      const res = await apiFetch('/api/workflows/');
      if (res.ok) setWorkflows(await res.json());
    } catch {
      // Non-fatal: the filter just won't have workflow options.
    }
  }, []);

  const fetchLeads = useCallback(async () => {
    setIsLoading(true);
    try {
      const params = new URLSearchParams();
      params.set('skip', String(page * PAGE_SIZE));
      params.set('limit', String(PAGE_SIZE));
      if (status) params.set('status', status);
      if (workflowId) params.set('workflow_id', workflowId);
      if (search.trim()) params.set('search', search.trim());
      const res = await apiFetch(`/api/leads?${params.toString()}`);
      if (res.ok) {
        const data: Lead[] = await res.json();
        setLeads(data);
        setHasMore(data.length === PAGE_SIZE);
      } else {
        const err = await res.json().catch(() => ({}));
        toast.error(formatApiDetail(err.detail, t('Failed to load leads')));
      }
    } catch {
      toast.error(t('Network error loading leads'));
    } finally {
      setIsLoading(false);
    }
  }, [page, status, workflowId, search, t]);

  useEffect(() => { fetchWorkflows(); }, [fetchWorkflows]);
  useEffect(() => { fetchLeads(); }, [fetchLeads]);

  // Reset to first page whenever a filter changes.
  useEffect(() => { setPage(0); }, [status, workflowId, search]);

  const submitSearch = (e: React.FormEvent) => {
    e.preventDefault();
    setSearch(searchInput);
  };

  const clearFilters = () => {
    setSearchInput('');
    setSearch('');
    setStatus('');
    setWorkflowId('');
  };

  const rateLead = async (lead: Lead, rating: 'positive' | 'negative') => {
    setRatingId(lead.id);
    // Optimistic update so the thumbs feel instant.
    setLeads(prev => prev.map(l => l.id === lead.id ? { ...l, user_rating: rating } : l));
    try {
      const res = await apiFetch(`/api/leads/${lead.id}/rate`, {
        method: 'POST',
        body: JSON.stringify({ rating }),
      });
      if (res.ok) {
        toast.success(rating === 'positive' ? t('Marked as a good lead') : t('Marked as a poor lead'));
      } else {
        setLeads(prev => prev.map(l => l.id === lead.id ? { ...l, user_rating: lead.user_rating } : l));
        const err = await res.json().catch(() => ({}));
        toast.error(formatApiDetail(err.detail, t('Failed to rate lead')));
      }
    } catch {
      setLeads(prev => prev.map(l => l.id === lead.id ? { ...l, user_rating: lead.user_rating } : l));
      toast.error(t('Network error rating lead'));
    } finally {
      setRatingId(null);
    }
  };

  const openDetail = async (lead: Lead) => {
    setDetailLead(lead);
    setBrief(null);
    setBriefLoading(true);
    try {
      const res = await apiFetch(`/api/leads/${lead.id}/brief`);
      if (res.ok) setBrief(await res.json());
    } catch {
      // No brief is a normal case — the dialog handles a null brief.
    } finally {
      setBriefLoading(false);
    }
  };

  const filtersActive = Boolean(search || status || workflowId);

  return (
    <div>
      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:justify-between sm:items-end">
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-indigo-600">{t('Workspace')}</p>
          <h1 className="text-2xl font-semibold tracking-tight text-white sm:text-3xl">{t('Leads')}</h1>
          <p className="mt-2 text-sm text-slate-500">{t('Every lead across all workflows and pools, searchable in one place.')}</p>
        </div>
        <Button onClick={fetchLeads} variant="outline" className="gap-2 bg-transparent text-slate-700 border-slate-300">
          <RefreshCw className="w-4 h-4" /> {t('Refresh')}
        </Button>
      </div>

      {/* Filter bar */}
      <div className="glass-panel mb-5 rounded-lg p-4">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
          <form onSubmit={submitSearch} className="relative flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
            <Input
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder={t('Search company, domain, email, name...')}
              className="pl-9"
            />
          </form>
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            className="h-9 rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-950 outline-none focus-visible:border-indigo-400"
          >
            <option value="">{t('All statuses')}</option>
            {STATUS_OPTIONS.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
          <select
            value={workflowId}
            onChange={(e) => setWorkflowId(e.target.value)}
            className="h-9 rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-950 outline-none focus-visible:border-indigo-400"
          >
            <option value="">{t('All workflows')}</option>
            {workflows.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}
          </select>
          {filtersActive && (
            <Button variant="ghost" size="sm" onClick={clearFilters} className="gap-1 text-slate-500">
              <X className="h-3.5 w-3.5" /> {t('Clear')}
            </Button>
          )}
        </div>
      </div>

      {/* List */}
      {isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="glass-panel flex animate-pulse items-center gap-4 rounded-lg p-4">
              <div className="h-10 w-10 rounded-full bg-slate-200/60" />
              <div className="flex-1 space-y-2">
                <div className="h-3.5 w-1/3 rounded bg-slate-200/60" />
                <div className="h-3 w-1/2 rounded bg-slate-200/40" />
              </div>
              <div className="h-6 w-16 rounded bg-slate-200/50" />
            </div>
          ))}
        </div>
      ) : leads.length === 0 ? (
        <div className="glass-panel rounded-lg border border-dashed border-slate-300 p-12 text-center text-slate-500">
          <Database className="mx-auto mb-4 h-12 w-12 opacity-50" />
          <p className="mb-1 font-medium text-white">{filtersActive ? t('No leads match these filters') : t('No leads yet')}</p>
          <p className="text-sm">
            {filtersActive
              ? t('Try clearing filters or widening your search.')
              : t('Activate a workflow and the prospecting engine will start finding leads.')}
          </p>
          {filtersActive && (
            <Button variant="outline" size="sm" onClick={clearFilters} className="mt-4 bg-transparent">
              {t('Clear filters')}
            </Button>
          )}
        </div>
      ) : (
        <>
          <div className="space-y-2.5">
            {leads.map(lead => {
              const name = `${lead.first_name || ''} ${lead.last_name || ''}`.trim();
              const title = lead.company_name || lead.domain || t('Unknown company');
              return (
                <div key={lead.id} className="glass-panel rounded-lg p-4">
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
                    <div className="min-w-0 flex-1">
                      <button
                        onClick={() => openDetail(lead)}
                        className="group flex items-center gap-2 text-left"
                      >
                        <span className="truncate font-semibold text-white group-hover:text-indigo-500">{title}</span>
                        {lead.fit_grade && (
                          <Badge variant="secondary" className={cn('border', gradeClasses(lead.fit_grade))}>
                            {lead.fit_grade} · {lead.fit_score ?? '—'}
                          </Badge>
                        )}
                      </button>
                      <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-500">
                        {name && <span className="text-slate-500">{name}{lead.job_title ? ` · ${lead.job_title}` : ''}</span>}
                        {lead.email ? (
                          <span className="inline-flex items-center gap-1">
                            {lead.email_verified ? <MailCheck className="h-3 w-3 text-emerald-500" /> : <Mail className="h-3 w-3" />}
                            {lead.email}
                          </span>
                        ) : (
                          <span className="text-amber-600">{t('No email')}</span>
                        )}
                        {lead.source_channel && <span className="text-slate-500">· {lead.source_channel}</span>}
                      </div>
                      {lead.qualification_notes && (
                        <p className="mt-1.5 line-clamp-1 text-xs italic text-slate-500" title={lead.qualification_notes}>
                          {lead.qualification_notes}
                        </p>
                      )}
                    </div>

                    <span className={cn('rounded px-2 py-0.5 text-xs font-semibold uppercase', statusClasses(lead.status))}>
                      {lead.status}
                    </span>

                    <div className="flex items-center gap-1">
                      <button
                        onClick={() => rateLead(lead, 'positive')}
                        disabled={ratingId === lead.id}
                        title={t('Good lead')}
                        className={cn('inline-flex h-8 w-8 items-center justify-center rounded-md transition-colors',
                          lead.user_rating === 'positive' ? 'bg-emerald-500/20 text-emerald-500' : 'text-slate-500 hover:bg-emerald-500/10 hover:text-emerald-500')}
                      >
                        <ThumbsUp className="h-4 w-4" />
                      </button>
                      <button
                        onClick={() => rateLead(lead, 'negative')}
                        disabled={ratingId === lead.id}
                        title={t('Poor lead')}
                        className={cn('inline-flex h-8 w-8 items-center justify-center rounded-md transition-colors',
                          lead.user_rating === 'negative' ? 'bg-rose-500/20 text-rose-500' : 'text-slate-500 hover:bg-rose-500/10 hover:text-rose-500')}
                      >
                        <ThumbsDown className="h-4 w-4" />
                      </button>
                      <button
                        onClick={() => openDetail(lead)}
                        title={t('Details')}
                        className="inline-flex h-8 w-8 items-center justify-center rounded-md text-slate-500 hover:bg-indigo-500/10 hover:text-indigo-500"
                      >
                        <FileText className="h-4 w-4" />
                      </button>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Pagination */}
          <div className="mt-5 flex items-center justify-between">
            <span className="text-xs text-slate-500">
              {t('Showing')} {page * PAGE_SIZE + 1}–{page * PAGE_SIZE + leads.length}
            </span>
            <div className="flex items-center gap-2">
              <Button
                variant="outline" size="sm"
                onClick={() => setPage(p => Math.max(0, p - 1))}
                disabled={page === 0}
                className="gap-1 bg-transparent"
              >
                <ChevronLeft className="h-4 w-4" /> {t('Previous')}
              </Button>
              <Button
                variant="outline" size="sm"
                onClick={() => setPage(p => p + 1)}
                disabled={!hasMore}
                className="gap-1 bg-transparent"
              >
                {t('Next')} <ChevronRight className="h-4 w-4" />
              </Button>
            </div>
          </div>
        </>
      )}

      {/* Detail dialog */}
      <Dialog open={detailLead !== null} onOpenChange={(o) => { if (!o) setDetailLead(null); }}>
        <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
          {detailLead && (
            <>
              <DialogHeader>
                <DialogTitle className="flex items-center gap-2">
                  {detailLead.company_name || detailLead.domain}
                  {detailLead.fit_grade && (
                    <Badge variant="secondary" className={cn('border', gradeClasses(detailLead.fit_grade))}>
                      {t('Fit')} {detailLead.fit_grade} · {detailLead.fit_score ?? '—'}
                    </Badge>
                  )}
                </DialogTitle>
              </DialogHeader>

              <div className="space-y-4 text-sm">
                <div className="grid grid-cols-2 gap-3">
                  <Field label={t('Contact')} value={`${detailLead.first_name || ''} ${detailLead.last_name || ''}`.trim() || '—'} />
                  <Field label={t('Role')} value={detailLead.job_title || '—'} />
                  <Field label={t('Email')} value={detailLead.email || t('No email')} />
                  <Field label={t('Status')} value={detailLead.status} />
                  <Field label={t('Source')} value={detailLead.source_channel || '—'} />
                  <Field label={t('Email verified')} value={detailLead.email_verified ? t('Yes') : (detailLead.email_validation_status || t('No'))} />
                </div>

                {detailLead.domain && (
                  <a
                    href={`https://${detailLead.domain}`} target="_blank" rel="noopener noreferrer"
                    className="inline-flex items-center gap-1 text-indigo-500 hover:underline"
                  >
                    {detailLead.domain} <ExternalLink className="h-3 w-3" />
                  </a>
                )}

                {detailLead.qualification_notes && (
                  <div className="rounded-lg border border-sky-500/20 bg-sky-500/5 p-3">
                    <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-sky-500">{t('Why this score')}</p>
                    <p className="text-slate-700">{detailLead.qualification_notes}</p>
                  </div>
                )}

                {detailLead.ai_draft && (
                  <div className="rounded-lg border border-indigo-500/20 bg-indigo-500/5 p-3">
                    <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-indigo-500">{t('AI draft')}</p>
                    <pre className="whitespace-pre-wrap font-sans text-slate-700">{detailLead.ai_draft}</pre>
                  </div>
                )}

                <div>
                  <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">{t('AI research brief')}</p>
                  {briefLoading ? (
                    <div className="flex items-center gap-2 text-slate-500"><Loader2 className="h-4 w-4 animate-spin" /> {t('Loading...')}</div>
                  ) : brief ? (
                    <div className="space-y-2 text-slate-700">
                      {brief.company_overview && <p>{brief.company_overview}</p>}
                      {brief.pain_points && <p><span className="text-slate-500">{t('Pain points')}: </span>{brief.pain_points}</p>}
                      {brief.personalization_hook && <p><span className="text-slate-500">{t('Hook')}: </span>{brief.personalization_hook}</p>}
                    </div>
                  ) : (
                    <p className="text-slate-500">{t('No research brief for this lead yet.')}</p>
                  )}
                </div>
              </div>
            </>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs text-slate-500">{label}</p>
      <p className="truncate text-slate-700" title={value}>{value}</p>
    </div>
  );
}
