"use client";

import { useEffect, useMemo, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Database, Plus, RefreshCw, Trash2, Download, Search, Mail as MailIcon, ThumbsUp, ThumbsDown, ShieldCheck, ShieldAlert, ShieldX } from 'lucide-react';
import { apiFetch } from '@/lib/utils';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import type { ClientPool, Lead } from '@/lib/types';

export default function PoolsPage() {
  const [pools, setPools] = useState<ClientPool[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  // Create Form State
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [newName, setNewName] = useState('');
  const [newDesc, setNewDesc] = useState('');
  const [newExcluded, setNewExcluded] = useState('');
  const [isCreating, setIsCreating] = useState(false);

  // Detail Modal State
  const [selectedPool, setSelectedPool] = useState<ClientPool | null>(null);
  const [leads, setLeads] = useState<Lead[]>([]);
  const [isLeadsLoading, setIsLeadsLoading] = useState(false);
  const [searchingPoolId, setSearchingPoolId] = useState<number | null>(null);
  const [searchMessage, setSearchMessage] = useState('');
  const [leadFilter, setLeadFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [ratingInFlight, setRatingInFlight] = useState<number | null>(null);
  const [scoringInFlight, setScoringInFlight] = useState<number | null>(null);

  const fetchPools = async () => {
    setIsLoading(true);
    try {
      const res = await apiFetch('/api/client_pools/');
      if (res.ok) {
        const data = await res.json();
        setPools(data);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchPools();
  }, []);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsCreating(true);
    try {
      const payload = {
        name: newName,
        description: newDesc,
        excluded_domains: newExcluded
      };
      const res = await apiFetch('/api/client_pools/', {
        method: 'POST',
        body: JSON.stringify(payload)
      });
      if (res.ok) {
        setIsCreateOpen(false);
        setNewName('');
        setNewDesc('');
        setNewExcluded('');
        fetchPools();
      }
    } catch (e) {
      console.error(e);
    } finally {
      setIsCreating(false);
    }
  };

  const deletePool = async (e: React.MouseEvent, id: number) => {
    e.stopPropagation();
    if(!confirm('确定要删除这个客户库吗？')) return;
    try {
      await apiFetch(`/api/client_pools/${id}`, { method: 'DELETE' });
      fetchPools();
    } catch(e) {
      console.error(e);
    }
  };

  const openPoolDetail = async (pool: ClientPool) => {
    setSelectedPool(pool);
    setIsLeadsLoading(true);
    try {
      const res = await apiFetch(`/api/client_pools/${pool.id}/leads?limit=1000`);
      if (res.ok) {
        const data = await res.json();
        setLeads(data);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setIsLeadsLoading(false);
    }
  };

  const exportPoolLeads = async () => {
    if (!selectedPool) {
      return
    }

    try {
      const res = await apiFetch(`/api/export/leads?pool_id=${selectedPool.id}`);
      if (!res.ok) {
        throw new Error('Failed to export leads')
      }

      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `${selectedPool.name.replace(/[^a-z0-9_-]+/gi, '_')}_leads.csv`
      document.body.appendChild(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(url)
    } catch (e) {
      console.error(e);
    }
  };

  const startPoolSearch = async (e: React.MouseEvent, pool: ClientPool) => {
    e.stopPropagation();
    setSearchingPoolId(pool.id);
    setSearchMessage('');
    try {
      const res = await apiFetch(`/api/client_pools/${pool.id}/search`, { method: 'POST' });
      const data = await res.json();
      setSearchMessage(data.message || 'Search started.');
      window.setTimeout(() => {
        fetchPools();
        if (selectedPool?.id === pool.id) {
          openPoolDetail(pool);
        }
      }, 4000);
    } catch (e) {
      console.error(e);
      setSearchMessage('Failed to start search.');
    } finally {
      setSearchingPoolId(null);
    }
  };

  const filteredLeads = useMemo(() => {
    const q = leadFilter.trim().toLowerCase();
    return leads.filter(lead => {
      if (statusFilter !== 'all' && lead.status !== statusFilter) return false;
      if (!q) return true;
      const name = [lead.first_name, lead.last_name].filter(Boolean).join(' ').toLowerCase();
      return (
        name.includes(q) ||
        (lead.company_name || '').toLowerCase().includes(q) ||
        (lead.domain || '').toLowerCase().includes(q) ||
        (lead.email || '').toLowerCase().includes(q) ||
        (lead.job_title || '').toLowerCase().includes(q)
      );
    });
  }, [leads, leadFilter, statusFilter]);

  const rateLead = async (leadId: number, rating: 'positive' | 'negative') => {
    setRatingInFlight(leadId);
    try {
      const res = await apiFetch(`/api/leads/${leadId}/rate`, {
        method: 'POST',
        body: JSON.stringify({ rating })
      });
      if (res.ok) {
        setLeads(prev => prev.map(l => l.id === leadId ? { ...l, user_rating: rating } : l));
      }
    } catch (e) {
      console.error('Rate failed', e);
    } finally {
      setRatingInFlight(null);
    }
  };

  const scoreLead = async (leadId: number) => {
    setScoringInFlight(leadId);
    try {
      const res = await apiFetch(`/api/leads/${leadId}/score`, { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        setLeads(prev => prev.map(l => l.id === leadId ? {
          ...l,
          fit_score: data.fit_score,
          fit_grade: data.fit_grade,
          handoff_recommended: data.handoff_recommended,
          qualification_notes: data.qualification_notes,
        } : l));
      }
    } catch (e) {
      console.error('Scoring failed', e);
    } finally {
      setScoringInFlight(null);
    }
  };

  return (
    <div>
      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:justify-between sm:items-end">
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-indigo-600">Workspace</p>
          <h1 className="text-2xl font-semibold tracking-tight text-white sm:text-3xl">Client Pools</h1>
          <p className="mt-2 text-sm text-gray-400">Manage your target audiences and deduplicate leads automatically.</p>
        </div>
        <div className="flex flex-wrap gap-3">
          <Button onClick={fetchPools} variant="outline" className="gap-2 bg-transparent text-slate-700 border-white/20">
            <RefreshCw className="w-4 h-4" /> Refresh
          </Button>

          <Dialog open={isCreateOpen} onOpenChange={setIsCreateOpen}>
            <DialogTrigger asChild>
              <Button className="gap-2 bg-indigo-600 hover:bg-indigo-700 text-white border-0">
                <Plus className="w-4 h-4" /> New Pool
              </Button>
            </DialogTrigger>
            <DialogContent className="sm:max-w-[425px]">
              <DialogHeader>
                <DialogTitle>Create Client Pool</DialogTitle>
              </DialogHeader>
              <form onSubmit={handleCreate} className="space-y-4 mt-4">
                <div className="space-y-2">
                  <Label htmlFor="name">Pool Name *</Label>
                  <Input id="name" required value={newName} onChange={e => setNewName(e.target.value)} placeholder="e.g. Europe Sports Equipment" />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="desc">Description</Label>
                  <Textarea id="desc" value={newDesc} onChange={e => setNewDesc(e.target.value)} placeholder="Optional notes..." />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="excluded">Excluded Domains</Label>
                  <Input id="excluded" value={newExcluded} onChange={e => setNewExcluded(e.target.value)} placeholder="e.g. competitor.com, bad.de" />
                  <p className="text-xs text-muted-foreground">Comma-separated. AutoLeadGen will skip searching these domains.</p>
                </div>
                <div className="pt-4 flex justify-end">
                  <Button type="submit" disabled={isCreating} className="bg-indigo-600 hover:bg-indigo-700 text-white">
                    {isCreating ? 'Creating...' : 'Save Pool'}
                  </Button>
                </div>
              </form>
            </DialogContent>
          </Dialog>
        </div>
      </div>
      {searchMessage && (
        <div className="mb-4 rounded-lg border border-emerald-500/20 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-700">
          {searchMessage}
        </div>
      )}

      {isLoading ? (
        <div className="py-20 text-center text-gray-500">Loading pools...</div>
      ) : pools.length === 0 ? (
        <div className="glass-panel p-12 text-center text-gray-400 rounded-lg border border-dashed border-white/20">
          <Database className="w-12 h-12 mx-auto mb-4 opacity-50" />
          <p>No client pools created yet. Click &quot;New Pool&quot; to get started.</p>
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {pools.map(pool => (
            <div 
              key={pool.id} 
              onClick={() => openPoolDetail(pool)}
              className="glass-panel p-5 rounded-lg flex flex-col justify-between cursor-pointer hover:border-indigo-500/50 hover:shadow-[0_12px_32px_rgba(79,70,229,0.12)] transition-all"
            >
              <div>
                <div className="flex justify-between items-start mb-2">
                  <h3 className="font-bold text-lg text-white">{pool.name}</h3>
                  <div className="flex items-center gap-2">
                    <button onClick={(e) => startPoolSearch(e, pool)} disabled={searchingPoolId === pool.id} className="text-gray-500 hover:text-emerald-500 disabled:opacity-50 transition-colors z-10" title="Search leads now">
                      <Search className="w-4 h-4" />
                    </button>
                    <button onClick={(e) => deletePool(e, pool.id)} className="text-gray-500 hover:text-rose-500 transition-colors z-10" title="Delete pool">
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>
                <p className="text-sm text-gray-400 mb-4">{pool.description || 'No description provided.'}</p>
                {pool.excluded_domains && (
                  <div className="text-xs text-rose-400/80 bg-rose-400/10 inline-block px-2 py-1 rounded mb-4">
                    Excluded: {pool.excluded_domains}
                  </div>
                )}
              </div>
              <div className="grid grid-cols-2 gap-4 mt-4 pt-4 border-t border-white/10">
                <div>
                  <div className="text-2xl font-bold text-slate-900">{pool.total_leads || 0}</div>
                  <div className="text-xs text-gray-500 uppercase tracking-wider">Total Leads</div>
                </div>
                <div>
                  <div className="text-2xl font-bold text-indigo-500">{pool.contacted_leads || 0}</div>
                  <div className="text-xs text-gray-500 uppercase tracking-wider">Contacted</div>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Pool Detail Dialog */}
      <Dialog open={!!selectedPool} onOpenChange={(open) => {
        if (!open) {
          setSelectedPool(null);
          setLeadFilter('');
          setStatusFilter('all');
        }
      }}>
        <DialogContent className="w-[96vw] max-w-[1400px] sm:max-w-[1400px] h-[90vh] max-h-[90vh] flex flex-col bg-white border border-slate-200 text-slate-900 shadow-xl p-0 sm:p-0">
          <DialogHeader className="px-6 pt-5 pb-3 border-b border-slate-200 shrink-0">
            <DialogTitle className="flex flex-col gap-3 sm:flex-row sm:justify-between sm:items-start pr-8">
              <div className="min-w-0">
                <div className="text-xs font-semibold uppercase tracking-[0.18em] text-indigo-600">Client Pool</div>
                <div className="mt-1 text-xl font-semibold text-slate-900 truncate">{selectedPool?.name}</div>
                {selectedPool?.description && (
                  <div className="mt-1 text-sm font-normal text-slate-500 line-clamp-2">{selectedPool.description}</div>
                )}
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <Button onClick={exportPoolLeads} variant="outline" size="sm" className="gap-2 bg-transparent border-slate-200">
                  <Download className="w-4 h-4" /> Export CSV
                </Button>
                {selectedPool && (
                  <Button onClick={(e) => startPoolSearch(e, selectedPool)} disabled={searchingPoolId === selectedPool.id} variant="outline" size="sm" className="gap-2 bg-transparent border-slate-200">
                    <Search className="w-4 h-4" /> {searchingPoolId === selectedPool.id ? 'Searching...' : 'Search Now'}
                  </Button>
                )}
              </div>
            </DialogTitle>
          </DialogHeader>

          {/* Stats + filter */}
          <div className="px-6 py-4 border-b border-slate-200 shrink-0 grid gap-3 sm:grid-cols-2 lg:grid-cols-[1fr_auto] items-center">
            <div className="grid grid-cols-4 gap-3">
              <div className="rounded-lg border border-slate-200 bg-slate-50/60 px-3 py-2">
                <div className="text-[10px] uppercase tracking-wider text-slate-500">Showing</div>
                <div className="text-lg font-semibold text-slate-900">{filteredLeads.length}<span className="text-xs font-normal text-slate-400"> / {leads.length}</span></div>
              </div>
              <div className="rounded-lg border border-slate-200 bg-slate-50/60 px-3 py-2">
                <div className="text-[10px] uppercase tracking-wider text-slate-500">Total Leads</div>
                <div className="text-lg font-semibold text-slate-900">{selectedPool?.total_leads ?? leads.length}</div>
              </div>
              <div className="rounded-lg border border-emerald-100 bg-emerald-50/60 px-3 py-2">
                <div className="text-[10px] uppercase tracking-wider text-emerald-700">Contacted</div>
                <div className="text-lg font-semibold text-emerald-700">{selectedPool?.contacted_leads ?? 0}</div>
              </div>
              <div className="rounded-lg border border-indigo-100 bg-indigo-50/60 px-3 py-2">
                <div className="text-[10px] uppercase tracking-wider text-indigo-700">Replied</div>
                <div className="text-lg font-semibold text-indigo-700">{selectedPool?.replied_leads ?? 0}</div>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2 justify-end">
              <div className="flex flex-wrap gap-1">
                {['all', 'found', 'drafted', 'sent', 'replied', 'needs_email'].map(s => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => setStatusFilter(s)}
                    className={
                      'px-2.5 py-1 rounded-md text-xs font-medium transition-colors ' +
                      (statusFilter === s
                        ? 'bg-indigo-600 text-white'
                        : 'bg-slate-100 text-slate-600 hover:bg-slate-200')
                    }
                  >
                    {s === 'all' ? 'All' : s.charAt(0).toUpperCase() + s.slice(1)}
                  </button>
                ))}
              </div>
              <Input
                placeholder="Search name / company / domain / email"
                value={leadFilter}
                onChange={e => setLeadFilter(e.target.value)}
                className="w-full sm:w-64 h-9"
              />
            </div>
          </div>

          {/* Table area */}
          <div className="flex-1 overflow-auto">
            {isLeadsLoading ? (
              <div className="py-20 text-center text-slate-500">Loading leads...</div>
            ) : filteredLeads.length === 0 ? (
              <div className="py-20 text-center text-slate-500">
                {leads.length === 0 ? 'No leads in this pool yet.' : 'No leads match the current filter.'}
              </div>
            ) : (
              <table className="w-full caption-bottom text-sm">
                <thead className="sticky top-0 z-10 bg-white/95 backdrop-blur border-b border-slate-200">
                  <tr className="text-slate-500 text-left text-xs uppercase tracking-wider">
                    <th className="h-11 px-4 align-middle font-medium w-10">#</th>
                    <th className="h-11 px-4 align-middle font-medium">Contact</th>
                    <th className="h-11 px-4 align-middle font-medium">Company / Domain</th>
                    <th className="h-11 px-4 align-middle font-medium">Job Title</th>
                    <th className="h-11 px-4 align-middle font-medium">Channels</th>
                    <th className="h-11 px-4 align-middle font-medium">Status</th>
                    <th className="h-11 px-4 align-middle font-medium">Fit</th>
                    <th className="h-11 px-4 align-middle font-medium text-center" title="Follow-ups sent">F/U</th>
                    <th className="h-11 px-4 align-middle font-medium">Last Reply</th>
                    <th className="h-11 px-4 align-middle font-medium">Added</th>
                    <th className="h-11 px-4 align-middle font-medium text-center">Quality</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredLeads.map((lead, idx) => (
                    <tr key={lead.id} className="border-b border-slate-100 transition-colors hover:bg-slate-50 align-top">
                      <td className="py-3 px-4 text-slate-400 text-xs">{idx + 1}</td>
                      <td className="py-3 px-4">
                        <div className="font-medium text-slate-900">
                          {[lead.first_name, lead.last_name].filter(Boolean).join(' ') || <span className="text-slate-400 italic">Company lead</span>}
                        </div>
                        {lead.email ? (
                          <div className="mt-0.5 inline-flex items-center gap-1.5">
                            {lead.email_validation_status === 'valid' && (
                              <span title="Verified"><ShieldCheck className="w-3.5 h-3.5 text-emerald-500 shrink-0" /></span>
                            )}
                            {lead.email_validation_status === 'invalid' && (
                              <span title="Invalid"><ShieldX className="w-3.5 h-3.5 text-rose-500 shrink-0" /></span>
                            )}
                            {lead.email_validation_status === 'catch-all' && (
                              <span title="Catch-all (risky)"><ShieldAlert className="w-3.5 h-3.5 text-amber-500 shrink-0" /></span>
                            )}
                            <a href={`mailto:${lead.email}`} className="text-xs text-slate-500 hover:text-indigo-600 break-all" onClick={e => e.stopPropagation()}>
                              <MailIcon className="w-3 h-3 shrink-0 inline mr-1" />{lead.email}
                            </a>
                          </div>
                        ) : (
                          <div className="text-xs text-slate-400 italic">No email yet</div>
                        )}
                      </td>
                      <td className="py-3 px-4">
                        <div className="font-medium text-slate-800">{lead.company_name || '—'}</div>
                        {lead.domain && (
                          <a
                            href={`https://${lead.domain}`}
                            target="_blank"
                            rel="noopener noreferrer"
                            onClick={e => e.stopPropagation()}
                            className="text-xs text-slate-500 hover:text-indigo-600 break-all"
                          >
                            {lead.domain}
                          </a>
                        )}
                      </td>
                      <td className="py-3 px-4 text-slate-600">{lead.job_title || '—'}</td>
                      <td className="py-3 px-4">
                        <div className="flex items-center gap-2">
                          {lead.linkedin_url ? (
                            <a
                              href={lead.linkedin_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              onClick={e => e.stopPropagation()}
                              title="View LinkedIn profile"
                              className="inline-flex h-7 w-7 items-center justify-center rounded-md bg-[#0a66c2]/10 text-[#0a66c2] hover:bg-[#0a66c2]/20 transition-colors"
                            >
                              <span className="text-xs font-bold">in</span>
                            </a>
                          ) : (
                            <span className="inline-flex h-7 w-7 items-center justify-center rounded-md bg-slate-100 text-slate-300" title="No LinkedIn">
                              <span className="text-xs font-bold">in</span>
                            </span>
                          )}
                          {lead.email ? (
                            <span className="inline-flex h-7 w-7 items-center justify-center rounded-md bg-emerald-500/10 text-emerald-600" title="Email available">
                              <MailIcon className="w-3.5 h-3.5" />
                            </span>
                          ) : (
                            <span className="inline-flex h-7 w-7 items-center justify-center rounded-md bg-slate-100 text-slate-300" title="No email">
                              <MailIcon className="w-3.5 h-3.5" />
                            </span>
                          )}
                          {lead.workflow_id && (
                            <span title={`Workflow #${lead.workflow_id}`} className="inline-flex h-7 px-2 items-center rounded-md bg-slate-100 text-slate-500 text-[11px] font-medium">
                              WF #{lead.workflow_id}
                            </span>
                          )}
                        </div>
                        {lead.source_channel && (
                          <div className="mt-2 inline-flex rounded-md bg-slate-100 px-2 py-0.5 text-[11px] font-medium uppercase tracking-wide text-slate-500">
                            {lead.source_channel}
                          </div>
                        )}
                      </td>
                      <td className="py-3 px-4">
                        <Badge
                          variant="secondary"
                          className={statusBadgeClass(lead.status)}
                        >
                          {lead.status}
                        </Badge>
                      </td>
                      <td className="py-3 px-4">
                        <div className="flex flex-col gap-1">
                          {lead.fit_score !== null && lead.fit_score !== undefined ? (
                            <Badge variant="secondary" className={fitBadgeClass(lead.fit_grade || '')}>
                              {lead.fit_grade || '—'} · {lead.fit_score}
                            </Badge>
                          ) : (
                            <span className="text-xs text-slate-300">Unscored</span>
                          )}
                          {lead.handoff_recommended && (
                            <span className="inline-flex w-fit rounded-md bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700 ring-1 ring-amber-200">
                              Handoff
                            </span>
                          )}
                          {lead.qualification_notes && (
                            <span className="max-w-[180px] truncate text-[11px] text-slate-400" title={lead.qualification_notes}>
                              {lead.qualification_notes}
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="py-3 px-4 text-center text-slate-700">
                        {lead.followup_count ?? 0}
                      </td>
                      <td className="py-3 px-4 text-xs text-slate-600">
                        {lead.last_reply_at ? (
                          <div>
                            <div>{formatRelative(lead.last_reply_at)}</div>
                            {lead.reply_snippet && (
                              <div className="text-slate-400 mt-0.5 line-clamp-1 max-w-[220px]" title={lead.reply_snippet}>
                                &ldquo;{lead.reply_snippet}&rdquo;
                              </div>
                            )}
                          </div>
                        ) : (
                          <span className="text-slate-300">—</span>
                        )}
                      </td>
                      <td className="py-3 px-4 text-xs text-slate-500 whitespace-nowrap">
                        {lead.created_at ? formatDate(lead.created_at) : '—'}
                      </td>
                      <td className="py-3 px-4">
                        <div className="flex items-center justify-center gap-1">
                          <button
                            onClick={(e) => { e.stopPropagation(); rateLead(lead.id, 'positive'); }}
                            disabled={ratingInFlight === lead.id}
                            title="Good lead"
                            className={`p-1.5 rounded-md transition-all ${
                              lead.user_rating === 'positive'
                                ? 'bg-emerald-100 text-emerald-600 ring-1 ring-emerald-300'
                                : 'text-slate-300 hover:text-emerald-500 hover:bg-emerald-50'
                            }`}
                          >
                            <ThumbsUp className="w-3.5 h-3.5" />
                          </button>
                          <button
                            onClick={(e) => { e.stopPropagation(); rateLead(lead.id, 'negative'); }}
                            disabled={ratingInFlight === lead.id}
                            title="Bad lead"
                            className={`p-1.5 rounded-md transition-all ${
                              lead.user_rating === 'negative'
                                ? 'bg-rose-100 text-rose-600 ring-1 ring-rose-300'
                                : 'text-slate-300 hover:text-rose-500 hover:bg-rose-50'
                            }`}
                          >
                            <ThumbsDown className="w-3.5 h-3.5" />
                          </button>
                          <button
                            onClick={(e) => { e.stopPropagation(); scoreLead(lead.id); }}
                            disabled={scoringInFlight === lead.id}
                            title="Re-score fit"
                            className="p-1.5 rounded-md text-slate-300 hover:text-indigo-500 hover:bg-indigo-50 transition-all disabled:opacity-50"
                          >
                            <RefreshCw className={`w-3.5 h-3.5 ${scoringInFlight === lead.id ? 'animate-spin' : ''}`} />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// --- helpers ---
function statusBadgeClass(status: string) {
  switch (status) {
    case 'replied':
      return 'bg-indigo-100 text-indigo-700 border border-indigo-200';
    case 'sent':
    case 'contacted':
      return 'bg-emerald-100 text-emerald-700 border border-emerald-200';
    case 'drafted':
      return 'bg-violet-100 text-violet-700 border border-violet-200';
    case 'needs_email':
      return 'bg-amber-100 text-amber-700 border border-amber-200';
    case 'enriched':
      return 'bg-sky-100 text-sky-700 border border-sky-200';
    case 'failed':
    case 'send_failed':
    case 'invalid_email':
      return 'bg-rose-100 text-rose-700 border border-rose-200';
    case 'found':
    default:
      return 'bg-slate-100 text-slate-700 border border-slate-200';
  }
}

function fitBadgeClass(grade: string) {
  switch (grade) {
    case 'A':
      return 'bg-emerald-100 text-emerald-700 border border-emerald-200';
    case 'B':
      return 'bg-sky-100 text-sky-700 border border-sky-200';
    case 'C':
      return 'bg-amber-100 text-amber-700 border border-amber-200';
    case 'D':
      return 'bg-rose-100 text-rose-700 border border-rose-200';
    default:
      return 'bg-slate-100 text-slate-700 border border-slate-200';
  }
}

function formatDate(iso: string) {
  try {
    const d = new Date(iso);
    return d.toLocaleDateString('zh-CN', { year: '2-digit', month: '2-digit', day: '2-digit' });
  } catch {
    return '—';
  }
}

function formatRelative(iso: string) {
  try {
    const d = new Date(iso);
    const diff = (Date.now() - d.getTime()) / 1000;
    if (diff < 60) return `${Math.floor(diff)}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    if (diff < 86400 * 7) return `${Math.floor(diff / 86400)}d ago`;
    return formatDate(iso);
  } catch {
    return '—';
  }
}
