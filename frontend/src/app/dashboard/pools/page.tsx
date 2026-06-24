"use client";

import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from '@/lib/i18n';
import { Button } from '@/components/ui/button';
import { Database, Plus, RefreshCw, Trash2, Download, Search, Mail as MailIcon, ThumbsUp, ThumbsDown, ShieldCheck, ShieldAlert, ShieldX, Copy, Check, User, Building, Target, Zap, Sparkles, FileText, Upload, FileSpreadsheet, AlertCircle } from 'lucide-react';
import { apiFetch, formatApiDetail } from '@/lib/utils';
import { toast } from 'sonner';
import ConfirmDialog from '@/components/ConfirmDialog';
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
import type {
  ClientPool,
  CsvImportPreview,
  CsvImportResult,
  CsvLeadField,
  Lead,
  LeadBrief,
} from '@/lib/types';

const LEADS_PAGE_SIZE = 50;
const CSV_FIELD_LABELS: Record<CsvLeadField, [string, string]> = {
  company_name: ['Company', '公司名称'],
  domain: ['Domain / Website', '域名 / 网站'],
  email: ['Email', '邮箱'],
  first_name: ['First name', '名'],
  last_name: ['Last name', '姓'],
  job_title: ['Job title', '职位'],
  linkedin_url: ['LinkedIn URL', '领英链接'],
  whatsapp_number: ['WhatsApp / Phone', 'WhatsApp / 电话'],
};

export default function PoolsPage() {
  const { language } = useTranslation();
  const txt = (en: string, zh: string) => language === 'zh' ? zh : en;
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
  const [leadPage, setLeadPage] = useState(0);
  const [hasMoreLeads, setHasMoreLeads] = useState(false);
  const [ratingInFlight, setRatingInFlight] = useState<number | null>(null);
  const [scoringInFlight, setScoringInFlight] = useState<number | null>(null);

  // Bulk-selection state for batch operations on the lead table.
  const [selectedLeadIds, setSelectedLeadIds] = useState<Set<number>>(new Set());
  const [isBulkRunning, setIsBulkRunning] = useState(false);
  const [moveTargetId, setMoveTargetId] = useState<string>('');

  // Lead Brief Modal State
  const [selectedLead, setSelectedLead] = useState<Lead | null>(null);
  const [leadBrief, setLeadBrief] = useState<LeadBrief | null>(null);
  const [isBriefLoading, setIsBriefLoading] = useState(false);
  const [briefError, setBriefError] = useState<string | null>(null);
  const [copiedDraft, setCopiedDraft] = useState(false);
  const [sendingDraftId, setSendingDraftId] = useState<number | null>(null);
  const [sendDraftMessage, setSendDraftMessage] = useState('');

  // Delete confirmation state
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [deleteTargetId, setDeleteTargetId] = useState<number | null>(null);

  // CSV import state
  const [importPool, setImportPool] = useState<ClientPool | null>(null);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importPreview, setImportPreview] = useState<CsvImportPreview | null>(null);
  const [importMapping, setImportMapping] = useState<Record<CsvLeadField, string | null> | null>(null);
  const [isPreviewingImport, setIsPreviewingImport] = useState(false);
  const [isImporting, setIsImporting] = useState(false);

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

  const openDeleteDialog = (e: React.MouseEvent, id: number) => {
    e.stopPropagation();
    setDeleteTargetId(id);
    setDeleteDialogOpen(true);
  };

  const handleDeletePool = async () => {
    if (deleteTargetId === null) return;
    try {
      await apiFetch(`/api/client_pools/${deleteTargetId}`, { method: 'DELETE' });
      fetchPools();
    } catch(e) {
      console.error(e);
    } finally {
      setDeleteDialogOpen(false);
      setDeleteTargetId(null);
    }
  };

  const closeImportDialog = () => {
    setImportPool(null);
    setImportFile(null);
    setImportPreview(null);
    setImportMapping(null);
    setIsPreviewingImport(false);
    setIsImporting(false);
  };

  const openImportDialog = (event: React.MouseEvent, pool: ClientPool) => {
    event.stopPropagation();
    setImportPool(pool);
    setImportFile(null);
    setImportPreview(null);
    setImportMapping(null);
  };

  const openPoolDetail = (pool: ClientPool) => {
    setSelectedPool(pool);
    setLeadPage(0);
    setLeadFilter('');
    setStatusFilter('all');
  };

  const fetchPoolLeads = useCallback(async (
    poolId: number,
    page: number,
    status: string,
    search: string,
    signal?: AbortSignal,
  ) => {
    setIsLeadsLoading(true);
    const params = new URLSearchParams({
      skip: String(page * LEADS_PAGE_SIZE),
      limit: String(LEADS_PAGE_SIZE + 1),
    });
    if (status !== 'all') params.set('status', status);
    if (search.trim()) params.set('search', search.trim());

    try {
      const res = await apiFetch(`/api/client_pools/${poolId}/leads?${params.toString()}`, { signal });
      if (res.ok) {
        const data: Lead[] = await res.json();
        setLeads(data.slice(0, LEADS_PAGE_SIZE));
        setHasMoreLeads(data.length > LEADS_PAGE_SIZE);
      }
    } catch (e) {
      if (!(e instanceof DOMException && e.name === 'AbortError')) {
        console.error(e);
      }
    } finally {
      if (!signal?.aborted) setIsLeadsLoading(false);
    }
  }, []);

  const previewCsvImport = async (
    pool: ClientPool,
    file: File,
    mapping?: Record<CsvLeadField, string | null>,
  ) => {
    setIsPreviewingImport(true);
    try {
      const form = new FormData();
      form.append('file', file);
      if (mapping) form.append('mapping', JSON.stringify(mapping));
      const res = await apiFetch(`/api/client_pools/${pool.id}/import-preview`, {
        method: 'POST',
        body: form,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        toast.error(formatApiDetail(data.detail, txt('CSV preview failed.', 'CSV 预检失败。')));
        return;
      }
      const preview = data as CsvImportPreview;
      setImportPreview(preview);
      setImportMapping(preview.mapping);
    } catch (error) {
      console.error(error);
      toast.error(txt('Network error while reading CSV.', '读取 CSV 时发生网络错误。'));
    } finally {
      setIsPreviewingImport(false);
    }
  };

  const handleImportFile = async (file: File | null) => {
    setImportFile(file);
    setImportPreview(null);
    setImportMapping(null);
    if (file && importPool) {
      await previewCsvImport(importPool, file);
    }
  };

  const importCsvLeads = async () => {
    if (!importPool || !importFile || !importMapping) return;
    setIsImporting(true);
    try {
      const form = new FormData();
      form.append('file', importFile);
      form.append('mapping', JSON.stringify(importMapping));
      const res = await apiFetch(`/api/client_pools/${importPool.id}/import`, {
        method: 'POST',
        body: form,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        toast.error(formatApiDetail(data.detail, txt('CSV import failed.', 'CSV 导入失败。')));
        return;
      }
      const result = data as CsvImportResult;
      toast.success(txt(
        `Imported ${result.imported} leads; skipped ${result.duplicates} duplicates and ${result.invalid} invalid rows.`,
        `已导入 ${result.imported} 条线索；跳过 ${result.duplicates} 条重复和 ${result.invalid} 条无效数据。`,
      ));
      await fetchPools();
      if (selectedPool?.id === importPool.id) {
        await fetchPoolLeads(importPool.id, 0, statusFilter, leadFilter);
        setLeadPage(0);
      }
      closeImportDialog();
    } catch (error) {
      console.error(error);
      toast.error(txt('Network error during CSV import.', 'CSV 导入时发生网络错误。'));
    } finally {
      setIsImporting(false);
    }
  };

  useEffect(() => {
    if (!selectedPool) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      fetchPoolLeads(
        selectedPool.id,
        leadPage,
        statusFilter,
        leadFilter,
        controller.signal,
      );
    }, leadFilter ? 300 : 0);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [fetchPoolLeads, leadFilter, leadPage, selectedPool, statusFilter]);

  // Reset the selection whenever the visible set of leads changes.
  useEffect(() => {
    setSelectedLeadIds(new Set());
  }, [selectedPool, leadPage, statusFilter, leadFilter]);

  const toggleLeadSelected = (id: number) => {
    setSelectedLeadIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const toggleSelectAll = () => {
    setSelectedLeadIds(prev =>
      prev.size === leads.length ? new Set() : new Set(leads.map(l => l.id))
    );
  };

  const runBulkAction = async (action: 'score' | 'delete' | 'blacklist' | 'move_pool') => {
    const ids = Array.from(selectedLeadIds);
    if (ids.length === 0) return;
    if (action === 'move_pool' && !moveTargetId) {
      toast.error(txt('Choose a destination pool first.', '请先选择目标客户池。'));
      return;
    }
    setIsBulkRunning(true);
    try {
      const body: Record<string, unknown> = { lead_ids: ids, action };
      if (action === 'move_pool') body.target_pool_id = Number(moveTargetId);
      const res = await apiFetch('/api/leads/bulk/action', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        toast.error(formatApiDetail(data.detail, txt('Bulk action failed.', '批量操作失败。')));
        return;
      }
      const succeeded = data.succeeded ?? 0;
      const failed = data.failed ?? 0;
      const verb = action === 'score' ? txt('scored', '已评分')
        : action === 'delete' ? txt('deleted', '已删除')
        : action === 'blacklist' ? txt('blacklisted', '已加入黑名单')
        : txt('moved', '已移动');
      toast.success(
        failed > 0
          ? txt(`${succeeded} ${verb}, ${failed} skipped.`, `${succeeded} 条${verb}，${failed} 条跳过。`)
          : txt(`${succeeded} leads ${verb}.`, `${succeeded} 条线索${verb}。`)
      );
      setSelectedLeadIds(new Set());
      setMoveTargetId('');
      if (selectedPool) {
        await fetchPoolLeads(selectedPool.id, leadPage, statusFilter, leadFilter);
      }
      await fetchPools();
    } catch (error) {
      console.error(error);
      toast.error(txt('Network error during bulk action.', '批量操作时发生网络错误。'));
    } finally {
      setIsBulkRunning(false);
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
          fetchPoolLeads(pool.id, leadPage, statusFilter, leadFilter);
        }
      }, 4000);
    } catch (e) {
      console.error(e);
      setSearchMessage('Failed to start search.');
    } finally {
      setSearchingPoolId(null);
    }
  };

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

  const openLeadBrief = async (lead: Lead) => {
    setSelectedLead(lead);
    setLeadBrief(null);
    setBriefError(null);
    setSendDraftMessage('');
    setIsBriefLoading(true);
    try {
      let res = await apiFetch(`/api/leads/${lead.id}/brief`);
      // No brief yet — run AI deep-research on demand to generate one.
      if (res.status === 404) {
        res = await apiFetch(`/api/leads/${lead.id}/brief`, { method: 'POST' });
      }
      if (res.ok) {
        const data = await res.json();
        setLeadBrief(data);
      } else {
        const err = await res.json().catch(() => ({}));
        setBriefError(formatApiDetail(err.detail, '生成 AI 简介失败，请稍后重试。'));
      }
    } catch (e) {
      console.error(e);
      setBriefError('获取简介失败，请稍后重试。');
    } finally {
      setIsBriefLoading(false);
    }
  };

  const handleCopyDraft = (text: string) => {
    navigator.clipboard.writeText(text);
    setCopiedDraft(true);
    setTimeout(() => setCopiedDraft(false), 2000);
  };

  const sendReviewedDraft = async (lead: Lead) => {
    if (!lead.ai_draft) return;
    setSendingDraftId(lead.id);
    setSendDraftMessage('');
    try {
      const res = await apiFetch(`/api/leads/${lead.id}/send-draft`, {
        method: 'POST',
        body: JSON.stringify({ draft: lead.ai_draft })
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) {
        setSendDraftMessage(formatApiDetail(data.detail || data.message, txt('Send was blocked.', '发送已被拦截。')));
        return;
      }

      const updatedLead = { ...lead, status: data.status || 'sent' };
      setSelectedLead(updatedLead);
      setLeads(prev => prev.map(item => item.id === lead.id ? updatedLead : item));
      setSendDraftMessage(txt('Email sent successfully.', '邮件已发送。'));
    } catch (e) {
      console.error(e);
      setSendDraftMessage(txt('Network error while sending.', '发送时发生网络错误。'));
    } finally {
      setSendingDraftId(null);
    }
  };

  return (
    <div>
      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:justify-between sm:items-end">
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-indigo-600">{txt('Workspace', '工作区')}</p>
          <h1 className="text-2xl font-semibold tracking-tight text-white sm:text-3xl">{txt('Client Pools', '客户池')}</h1>
          <p className="mt-2 text-sm text-slate-500">{txt('Manage your target audiences and deduplicate leads automatically.', '管理您的目标客户群体并自动去重联系人。')}</p>
        </div>
        <div className="flex flex-wrap gap-3">
          <Button onClick={fetchPools} variant="outline" className="gap-2 bg-transparent text-slate-700 border-slate-300">
            <RefreshCw className="w-4 h-4" /> {txt('Refresh', '刷新')}
          </Button>

          <Dialog open={isCreateOpen} onOpenChange={setIsCreateOpen}>
            <DialogTrigger asChild>
              <Button className="gap-2 bg-indigo-600 hover:bg-indigo-700 text-white border-0">
                <Plus className="w-4 h-4" /> {txt('New Pool', '新建客户池')}
              </Button>
            </DialogTrigger>
            <DialogContent className="sm:max-w-[425px]">
              <DialogHeader>
                <DialogTitle>{txt('Create Client Pool', '创建客户池')}</DialogTitle>
              </DialogHeader>
              <form onSubmit={handleCreate} className="space-y-4 mt-4">
                <div className="space-y-2">
                  <Label htmlFor="name">{txt('Pool Name *', '客户池名称 *')}</Label>
                  <Input id="name" required value={newName} onChange={e => setNewName(e.target.value)} placeholder="e.g. Europe Sports Equipment" />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="desc">{txt('Description', '描述')}</Label>
                  <Textarea id="desc" value={newDesc} onChange={e => setNewDesc(e.target.value)} placeholder="Optional notes..." />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="excluded">{txt('Excluded Domains', '排除域名')}</Label>
                  <Input id="excluded" value={newExcluded} onChange={e => setNewExcluded(e.target.value)} placeholder="e.g. competitor.com, bad.de" />
                  <p className="text-xs text-muted-foreground">{txt('Comma-separated. AutoLeadGen will skip searching these domains.', '用英文逗号分隔。AutoLeadGen 将自动跳过检索这些域名的客户。')}</p>
                </div>
                <div className="pt-4 flex justify-end">
                  <Button type="submit" disabled={isCreating} className="bg-indigo-600 hover:bg-indigo-700 text-white">
                    {isCreating ? txt('Creating...', '创建中...') : txt('Save Pool', '保存客户池')}
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
        <div className="py-20 text-center text-slate-500">{txt('Loading pools...', '正在加载客户池...')}</div>
      ) : pools.length === 0 ? (
        <div className="glass-panel p-12 text-center text-slate-500 rounded-lg border border-dashed border-slate-300">
          <Database className="w-12 h-12 mx-auto mb-4 opacity-50" />
          <p>{txt('No client pools created yet. Click "New Pool" to get started.', '暂无已创建的客户池。点击“新建客户池”开始吧。')}</p>
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
                    <button onClick={(e) => openImportDialog(e, pool)} className="text-slate-500 hover:text-indigo-500 transition-colors z-10" title={txt('Import CSV', '导入 CSV')}>
                      <Upload className="w-4 h-4" />
                    </button>
                    <button onClick={(e) => startPoolSearch(e, pool)} disabled={searchingPoolId === pool.id} className="text-slate-500 hover:text-emerald-500 disabled:opacity-50 transition-colors z-10" title={txt('Search leads now', '立即搜索联系人')}>
                      <Search className="w-4 h-4" />
                    </button>
                    <button onClick={(e) => openDeleteDialog(e, pool.id)} className="text-slate-500 hover:text-rose-500 transition-colors z-10" title={txt('Delete pool', '删除客户库')}>
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>
                <p className="text-sm text-slate-500 mb-4">{pool.description || txt('No description provided.', '暂无描述。')}</p>
                {pool.excluded_domains && (
                  <div className="text-xs text-rose-400/80 bg-rose-400/10 inline-block px-2 py-1 rounded mb-4">
                    {txt('Excluded: ', '已排除: ')}{pool.excluded_domains}
                  </div>
                )}
              </div>
              <div className="grid grid-cols-2 gap-4 mt-4 pt-4 border-t border-slate-200">
                <div>
                  <div className="text-2xl font-bold text-slate-900">{pool.total_leads || 0}</div>
                  <div className="text-xs text-slate-500 uppercase tracking-wider">{txt('Total Leads', '总线索')}</div>
                </div>
                <div>
                  <div className="text-2xl font-bold text-indigo-500">{pool.contacted_leads || 0}</div>
                  <div className="text-xs text-slate-500 uppercase tracking-wider">{txt('Contacted', '已联系')}</div>
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
          setLeadPage(0);
          setLeads([]);
          setHasMoreLeads(false);
        }
      }}>
        <DialogContent className="w-[96vw] max-w-[1400px] sm:max-w-[1400px] h-[90vh] max-h-[90vh] flex flex-col bg-white border border-slate-200 text-slate-900 shadow-xl p-0 sm:p-0">
          <DialogHeader className="px-6 pt-5 pb-3 border-b border-slate-200 shrink-0">
            <DialogTitle className="flex flex-col gap-3 sm:flex-row sm:justify-between sm:items-start pr-8">
              <div className="min-w-0">
                <div className="text-xs font-semibold uppercase tracking-[0.18em] text-indigo-600">{txt('Client Pool', '客户池')}</div>
                <div className="mt-1 text-xl font-semibold text-slate-900 truncate">{selectedPool?.name}</div>
                {selectedPool?.description && (
                  <div className="mt-1 text-sm font-normal text-slate-500 line-clamp-2">{selectedPool.description}</div>
                )}
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {selectedPool && (
                  <Button onClick={(e) => openImportDialog(e, selectedPool)} variant="outline" size="sm" className="gap-2 bg-transparent border-slate-200">
                    <Upload className="w-4 h-4" /> {txt('Import CSV', '导入 CSV')}
                  </Button>
                )}
                <Button onClick={exportPoolLeads} variant="outline" size="sm" className="gap-2 bg-transparent border-slate-200">
                  <Download className="w-4 h-4" /> {txt('Export CSV', '导出 CSV')}
                </Button>
                {selectedPool && (
                  <Button onClick={(e) => startPoolSearch(e, selectedPool)} disabled={searchingPoolId === selectedPool.id} variant="outline" size="sm" className="gap-2 bg-transparent border-slate-200">
                    <Search className="w-4 h-4" /> {searchingPoolId === selectedPool.id ? txt('Searching...', '搜索中...') : txt('Search Now', '立即搜索')}
                  </Button>
                )}
              </div>
            </DialogTitle>
          </DialogHeader>

          {/* Stats + filter */}
          <div className="px-6 py-4 border-b border-slate-200 shrink-0 grid gap-3 sm:grid-cols-2 lg:grid-cols-[1fr_auto] items-center">
            <div className="grid grid-cols-4 gap-3" translate="no">
              <div className="rounded-lg border border-slate-200 bg-slate-50/60 px-3 py-2">
                <div className="text-[10px] uppercase tracking-wider text-slate-500">{txt('Showing', '显示')}</div>
                <div className="text-lg font-semibold text-slate-900">
                  <span>{leads.length}</span>
                  <span className="text-xs font-normal text-slate-400"> / </span>
                  <span>{txt(`page ${leadPage + 1}`, `第 ${leadPage + 1} 页`)}</span>
                </div>
              </div>
              <div className="rounded-lg border border-slate-200 bg-slate-50/60 px-3 py-2">
                <div className="text-[10px] uppercase tracking-wider text-slate-500">{txt('Total Leads', '总线索')}</div>
                <div className="text-lg font-semibold text-slate-900">
                  <span>{selectedPool?.total_leads ?? leads.length}</span>
                </div>
              </div>
              <div className="rounded-lg border border-emerald-100 bg-emerald-50/60 px-3 py-2">
                <div className="text-[10px] uppercase tracking-wider text-emerald-700">{txt('Contacted', '已联系')}</div>
                <div className="text-lg font-semibold text-emerald-700">
                  <span>{selectedPool?.contacted_leads ?? 0}</span>
                </div>
              </div>
              <div className="rounded-lg border border-indigo-100 bg-indigo-50/60 px-3 py-2">
                <div className="text-[10px] uppercase tracking-wider text-indigo-700">{txt('Replied', '已回复')}</div>
                <div className="text-lg font-semibold text-indigo-700">
                  <span>{selectedPool?.replied_leads ?? 0}</span>
                </div>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2 justify-end">
              <div className="flex flex-wrap gap-1">
                {['all', 'found', 'drafted', 'sent', 'replied', 'needs_email', 'low_score'].map(s => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => {
                      setStatusFilter(s);
                      setLeadPage(0);
                    }}
                    className={
                      'px-2.5 py-1 rounded-md text-xs font-medium transition-colors ' +
                      (statusFilter === s
                        ? 'bg-indigo-600 text-white'
                        : 'bg-slate-100 text-slate-600 hover:bg-slate-200')
                    }
                  >
                    {txt(
                      s === 'all' ? 'All' : s.charAt(0).toUpperCase() + s.slice(1),
                      s === 'all' ? '全部' :
                      s === 'found' ? '已挖掘' :
                      s === 'drafted' ? '草稿中' :
                      s === 'sent' ? '已发送' :
                      s === 'replied' ? '已回复' :
                      s === 'needs_email' ? '缺邮箱' :
                      s === 'low_score' ? '低分' : s
                    )}
                  </button>
                ))}
              </div>
              <Input
                placeholder={txt('Search name / company / domain / email', '搜索姓名/公司/域名/邮箱')}
                value={leadFilter}
                onChange={e => {
                  setLeadFilter(e.target.value);
                  setLeadPage(0);
                }}
                className="w-full sm:w-64 h-9"
              />
            </div>
          </div>

          {/* Bulk action bar — appears once leads are selected */}
          {selectedLeadIds.size > 0 && (
            <div className="flex flex-wrap items-center gap-2 border-b border-indigo-100 bg-indigo-50/70 px-4 py-2.5">
              <span className="text-sm font-medium text-indigo-700">
                {txt(`${selectedLeadIds.size} selected`, `已选 ${selectedLeadIds.size} 条`)}
              </span>
              <button
                onClick={() => setSelectedLeadIds(new Set())}
                className="text-xs text-slate-500 hover:text-slate-700 underline"
              >
                {txt('Clear', '清除')}
              </button>
              <div className="mx-1 h-4 w-px bg-indigo-200" />
              <Button size="sm" variant="outline" disabled={isBulkRunning}
                onClick={() => runBulkAction('score')}>
                <Zap className="mr-1 h-3.5 w-3.5" /> {txt('Score', '评分')}
              </Button>
              <Button size="sm" variant="outline" disabled={isBulkRunning}
                onClick={() => runBulkAction('blacklist')}>
                <ShieldX className="mr-1 h-3.5 w-3.5" /> {txt('Blacklist', '加黑名单')}
              </Button>
              <div className="flex items-center gap-1">
                <select
                  value={moveTargetId}
                  onChange={e => setMoveTargetId(e.target.value)}
                  className="h-8 rounded-md border border-slate-300 bg-white px-2 text-xs text-slate-700"
                >
                  <option value="">{txt('Move to…', '移动到…')}</option>
                  {pools.filter(p => p.id !== selectedPool?.id).map(p => (
                    <option key={p.id} value={p.id}>{p.name}</option>
                  ))}
                </select>
                <Button size="sm" variant="outline" disabled={isBulkRunning || !moveTargetId}
                  onClick={() => runBulkAction('move_pool')}>
                  {txt('Move', '移动')}
                </Button>
              </div>
              <Button size="sm" variant="outline" disabled={isBulkRunning}
                className="border-rose-200 text-rose-600 hover:bg-rose-50 hover:text-rose-700"
                onClick={() => runBulkAction('delete')}>
                <Trash2 className="mr-1 h-3.5 w-3.5" /> {txt('Delete', '删除')}
              </Button>
              {isBulkRunning && <RefreshCw className="h-4 w-4 animate-spin text-indigo-500" />}
            </div>
          )}

          {/* Table area */}
          <div className="flex-1 overflow-auto" translate="no">
            {isLeadsLoading ? (
              <div className="py-20 text-center text-slate-500">{txt('Loading leads...', '正在加载联系人列表...')}</div>
            ) : leads.length === 0 ? (
              <div className="py-20 text-center text-slate-500">
                {leadFilter || statusFilter !== 'all'
                  ? txt('No leads match the current filter.', '没有匹配当前筛选条件的联系人。')
                  : txt('No leads in this pool yet.', '该客户池暂无联系人。')}
              </div>
            ) : (
              <table className="w-full caption-bottom text-sm">
                <thead className="sticky top-0 z-10 bg-white/95 backdrop-blur border-b border-slate-200">
                  <tr className="text-slate-500 text-left text-xs uppercase tracking-wider">
                    <th className="h-11 px-4 align-middle font-medium w-10">
                      <input
                        type="checkbox"
                        aria-label={txt('Select all', '全选')}
                        className="h-4 w-4 cursor-pointer rounded border-slate-300 accent-indigo-600"
                        checked={leads.length > 0 && selectedLeadIds.size === leads.length}
                        ref={el => { if (el) el.indeterminate = selectedLeadIds.size > 0 && selectedLeadIds.size < leads.length; }}
                        onChange={toggleSelectAll}
                      />
                    </th>
                    <th className="h-11 px-4 align-middle font-medium w-10">#</th>
                    <th className="h-11 px-4 align-middle font-medium">{txt('Contact', '联系人')}</th>
                    <th className="h-11 px-4 align-middle font-medium">{txt('Company / Domain', '公司 / 域名')}</th>
                    <th className="h-11 px-4 align-middle font-medium">{txt('Job Title', '职称')}</th>
                    <th className="h-11 px-4 align-middle font-medium">{txt('Channels', '渠道')}</th>
                    <th className="h-11 px-4 align-middle font-medium">{txt('Status', '状态')}</th>
                    <th className="h-11 px-4 align-middle font-medium min-w-[90px]">{txt('Fit', '匹配度')}</th>
                    <th className="h-11 px-4 align-middle font-medium text-center" title={txt('Follow-ups sent', '已发送跟进数')}>{txt('F/U', '跟进')}</th>
                    <th className="h-11 px-4 align-middle font-medium">{txt('Last Reply', '最后回复')}</th>
                    <th className="h-11 px-4 align-middle font-medium">{txt('Added', '添加时间')}</th>
                    <th className="h-11 px-4 align-middle font-medium text-center">{txt('Quality', '操作评分')}</th>
                  </tr>
                </thead>
                <tbody>
                  {leads.map((lead, idx) => (
                    <tr
                      key={lead.id}
                      onClick={() => openLeadBrief(lead)}
                      className={`border-b border-slate-100 transition-colors cursor-pointer align-top ${selectedLeadIds.has(lead.id) ? 'bg-indigo-50/60 hover:bg-indigo-50' : 'hover:bg-slate-50'}`}
                    >
                      <td className="py-3 px-4" onClick={e => e.stopPropagation()}>
                        <input
                          type="checkbox"
                          aria-label={txt('Select lead', '选择该线索')}
                          className="h-4 w-4 cursor-pointer rounded border-slate-300 accent-indigo-600"
                          checked={selectedLeadIds.has(lead.id)}
                          onChange={() => toggleLeadSelected(lead.id)}
                        />
                      </td>
                      <td className="py-3 px-4 text-slate-400 text-xs"><span>{leadPage * LEADS_PAGE_SIZE + idx + 1}</span></td>
                      <td className="py-3 px-4">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="font-medium text-slate-900">
                            {[lead.first_name, lead.last_name].filter(Boolean).join(' ') || <span className="text-slate-400 italic">{txt('Company lead', '公司联系人')}</span>}
                          </span>
                          <button
                            onClick={(e) => { e.stopPropagation(); openLeadBrief(lead); }}
                            className="inline-flex items-center gap-1 text-[10px] font-medium text-indigo-600 bg-indigo-50 hover:bg-indigo-100 hover:text-indigo-700 px-1.5 py-0.5 rounded border border-indigo-200/50 transition-all"
                            title={txt('查看 AI 简介与发信草稿', '查看 AI 简介与发信草稿')}
                          >
                            <Sparkles className="w-2.5 h-2.5" /> {txt('AI Brief', 'AI 简介')}
                          </button>
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
                          <div className="text-xs text-slate-400 italic">{txt('No email yet', '暂无邮箱')}</div>
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
                              title={txt('View LinkedIn profile', '查看领英档案')}
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
                            <span className="inline-flex h-7 w-7 items-center justify-center rounded-md bg-emerald-500/10 text-emerald-600" title={txt('Email available', '邮箱可用')}>
                              <MailIcon className="w-3.5 h-3.5" />
                            </span>
                          ) : (
                            <span className="inline-flex h-7 w-7 items-center justify-center rounded-md bg-slate-100 text-slate-300" title={txt('No email', '暂无邮箱')}>
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
                          {txt(
                            lead.status,
                            lead.status === 'found' ? '已挖掘' :
                            lead.status === 'drafted' ? '已起草' :
                            lead.status === 'sending' ? '发送中' :
                            lead.status === 'sent' ? '已发送' :
                            lead.status === 'replied' ? '已回复' :
                            lead.status === 'rejected' ? '已拒绝' :
                            lead.status === 'needs_email' ? '缺邮箱' :
                            lead.status === 'low_score' ? '低分' :
                            lead.status === 'failed' ? '发送失败' :
                            lead.status === 'provider_limited' ? '渠道受限' : lead.status
                          )}
                        </Badge>
                      </td>
                      <td className="py-3 px-4 whitespace-nowrap">
                        <div className="flex flex-col gap-1">
                          {lead.fit_score !== null && lead.fit_score !== undefined ? (
                            <Badge variant="secondary" className={fitBadgeClass(lead.fit_grade || '')}>
                              {lead.fit_grade || '—'} · {lead.fit_score}
                            </Badge>
                          ) : (
                            <span className="text-xs text-slate-300 whitespace-nowrap">{txt('Unscored', '未评分')}</span>
                          )}
                          {lead.handoff_recommended && (
                            <span className="inline-flex w-fit rounded-md bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700 ring-1 ring-amber-200">
                              {txt('Handoff', '已转交')}
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
                            title={txt('Good lead', '好线索')}
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
                            title={txt('Bad lead', '差线索')}
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
                            title={txt('Re-score fit', '重新评分')}
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
          <div className="flex items-center justify-between border-t border-slate-200 px-6 py-3 shrink-0">
            <span className="text-xs text-slate-500">
              {txt(
                `Showing ${leadPage * LEADS_PAGE_SIZE + (leads.length ? 1 : 0)}–${leadPage * LEADS_PAGE_SIZE + leads.length}`,
                `显示第 ${leadPage * LEADS_PAGE_SIZE + (leads.length ? 1 : 0)}–${leadPage * LEADS_PAGE_SIZE + leads.length} 条`,
              )}
            </span>
            <div className="flex gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={leadPage === 0 || isLeadsLoading}
                onClick={() => setLeadPage(page => Math.max(0, page - 1))}
              >
                {txt('Previous', '上一页')}
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={!hasMoreLeads || isLeadsLoading}
                onClick={() => setLeadPage(page => page + 1)}
              >
                {txt('Next', '下一页')}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* CSV Import Dialog */}
      <Dialog open={!!importPool} onOpenChange={(open) => {
        if (!open) closeImportDialog();
      }}>
        <DialogContent className="w-[96vw] max-w-[1000px] sm:max-w-[1000px] max-h-[90vh] overflow-y-auto bg-white">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <FileSpreadsheet className="h-5 w-5 text-indigo-600" />
              {txt('Import leads from CSV', '从 CSV 导入线索')}
            </DialogTitle>
          </DialogHeader>

          <div className="space-y-5">
            <div className="rounded-lg border border-slate-200 bg-slate-50 p-4">
              <p className="text-sm font-medium text-slate-900">{importPool?.name}</p>
              <p className="mt-1 text-xs text-slate-500">
                {txt(
                  'UTF-8 and GB18030 are supported. Maximum 2 MB / 5,000 rows. Existing email and domain duplicates are skipped.',
                  '支持 UTF-8 和 GB18030，单文件最大 2 MB / 5,000 行；已有邮箱和域名重复项会自动跳过。',
                )}
              </p>
            </div>

            <Input
              type="file"
              accept=".csv,.tsv,text/csv,text/tab-separated-values"
              onChange={event => handleImportFile(event.target.files?.[0] || null)}
            />

            {isPreviewingImport && (
              <div className="flex items-center gap-2 rounded-lg border border-indigo-100 bg-indigo-50 p-4 text-sm text-indigo-700">
                <RefreshCw className="h-4 w-4 animate-spin" />
                {txt('Reading and checking CSV...', '正在读取并检查 CSV...')}
              </div>
            )}

            {importPreview && importMapping && (
              <>
                <div className="grid gap-3 sm:grid-cols-4">
                  <ImportMetric label={txt('Rows', '总行数')} value={importPreview.total_rows} />
                  <ImportMetric label={txt('Ready', '可导入')} value={importPreview.counts.valid} tone="green" />
                  <ImportMetric label={txt('Duplicates', '重复')} value={importPreview.counts.duplicate} tone="amber" />
                  <ImportMetric label={txt('Invalid', '无效')} value={importPreview.counts.invalid} tone="red" />
                </div>

                <div>
                  <div className="mb-3 flex items-center justify-between gap-3">
                    <div>
                      <h3 className="text-sm font-semibold text-slate-900">{txt('Column mapping', '字段映射')}</h3>
                      <p className="text-xs text-slate-500">{txt('Map at least Email or Domain / Website.', '至少需要映射“邮箱”或“域名 / 网站”。')}</p>
                    </div>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={!importFile || isPreviewingImport}
                      onClick={() => importPool && importFile && previewCsvImport(importPool, importFile, importMapping)}
                    >
                      {txt('Recheck', '重新预检')}
                    </Button>
                  </div>
                  <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                    {importPreview.fields.map(field => (
                      <label key={field} className="space-y-1 text-xs font-medium text-slate-600">
                        <span>{txt(...CSV_FIELD_LABELS[field])}</span>
                        <select
                          value={importMapping[field] || ''}
                          onChange={event => setImportMapping(current => current ? {
                            ...current,
                            [field]: event.target.value || null,
                          } : current)}
                          className="h-9 w-full rounded-lg border border-slate-200 bg-white px-2 text-sm text-slate-900"
                        >
                          <option value="">{txt('Not mapped', '不映射')}</option>
                          {importPreview.headers.map(header => (
                            <option key={header} value={header}>{header}</option>
                          ))}
                        </select>
                      </label>
                    ))}
                  </div>
                </div>

                {importPreview.mapping_required && (
                  <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
                    <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                    {txt('Choose an Email or Domain column, then click Recheck.', '请选择邮箱或域名列，然后点击“重新预检”。')}
                  </div>
                )}

                {importPreview.preview_rows.length > 0 && (
                  <div className="overflow-hidden rounded-lg border border-slate-200">
                    <div className="border-b border-slate-200 bg-slate-50 px-4 py-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                      {txt('Preview (first 50 rows)', '预览（前 50 行）')}
                    </div>
                    <div className="max-h-[280px] overflow-auto">
                      <table className="w-full text-left text-xs">
                        <thead className="sticky top-0 bg-white text-slate-500">
                          <tr>
                            <th className="px-3 py-2">#</th>
                            <th className="px-3 py-2">{txt('Status', '状态')}</th>
                            <th className="px-3 py-2">{txt('Company', '公司')}</th>
                            <th className="px-3 py-2">{txt('Domain', '域名')}</th>
                            <th className="px-3 py-2">{txt('Email', '邮箱')}</th>
                            <th className="px-3 py-2">{txt('Reason', '原因')}</th>
                          </tr>
                        </thead>
                        <tbody>
                          {importPreview.preview_rows.map(row => (
                            <tr key={row.row_number} className="border-t border-slate-100">
                              <td className="px-3 py-2 text-slate-400">{row.row_number}</td>
                              <td className="px-3 py-2">
                                <span className={
                                  row.status === 'valid'
                                    ? 'text-emerald-600'
                                    : row.status === 'duplicate'
                                      ? 'text-amber-600'
                                      : 'text-rose-600'
                                }>
                                  {row.status}
                                </span>
                              </td>
                              <td className="px-3 py-2 text-slate-700">{row.normalized.company_name || '—'}</td>
                              <td className="px-3 py-2 text-slate-700">{row.normalized.domain || '—'}</td>
                              <td className="px-3 py-2 text-slate-700">{row.normalized.email || '—'}</td>
                              <td className="px-3 py-2 text-slate-500">{row.reason || '—'}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}

                <div className="flex justify-end gap-3 border-t border-slate-200 pt-4">
                  <Button variant="outline" onClick={closeImportDialog}>
                    {txt('Cancel', '取消')}
                  </Button>
                  <Button
                    onClick={importCsvLeads}
                    disabled={
                      isImporting
                      || isPreviewingImport
                      || importPreview.mapping_required
                      || importPreview.counts.valid === 0
                    }
                    className="gap-2"
                  >
                    {isImporting ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
                    {isImporting
                      ? txt('Importing...', '导入中...')
                      : txt(`Import ${importPreview.counts.valid} leads`, `导入 ${importPreview.counts.valid} 条线索`)}
                  </Button>
                </div>
              </>
            )}
          </div>
        </DialogContent>
      </Dialog>

      {/* Lead Brief Dialog */}
      <Dialog open={!!selectedLead} onOpenChange={(open) => {
        if (!open) {
          setSelectedLead(null);
          setLeadBrief(null);
          setBriefError(null);
        }
      }}>
        <DialogContent className="w-[96vw] max-w-[1200px] sm:max-w-[1200px] h-[85vh] max-h-[85vh] flex flex-col bg-white border border-slate-200 text-slate-900 shadow-2xl p-0">
          <DialogHeader className="px-6 pt-5 pb-4 border-b border-slate-100 shrink-0">
            <DialogTitle className="flex flex-col gap-1 pr-8">
              <div className="flex items-center gap-2">
                <Sparkles className="w-5 h-5 text-indigo-600 animate-pulse" />
                <span className="text-xs font-semibold uppercase tracking-[0.18em] text-indigo-600">{txt('Lead AI Research Brief', '客户 AI 背景调研简介')}</span>
              </div>
              <div className="mt-1 text-2xl font-bold text-slate-900">
                {[selectedLead?.first_name, selectedLead?.last_name].filter(Boolean).join(' ') || (
                  <span className="text-slate-400 italic">{txt('Company Representative', '公司代表')}</span>
                )}
              </div>
              <div className="flex items-center gap-4 text-sm font-normal text-slate-500 mt-1">
                {selectedLead?.company_name && (
                  <span className="flex items-center gap-1">
                    <Building className="w-4 h-4 text-slate-400" /> {selectedLead.company_name}
                  </span>
                )}
                {selectedLead?.job_title && (
                  <span className="flex items-center gap-1">
                    <User className="w-4 h-4 text-slate-400" /> {selectedLead.job_title}
                  </span>
                )}
                {selectedLead?.fit_grade && (
                  <Badge variant="secondary" className={fitBadgeClass(selectedLead.fit_grade)}>
                    {txt('Fit Score:', '匹配得分:')} {selectedLead.fit_score} ({selectedLead.fit_grade})
                  </Badge>
                )}
              </div>
            </DialogTitle>
          </DialogHeader>

          <div className="flex-1 overflow-y-auto p-6 space-y-6">
            {isBriefLoading ? (
              <div className="flex flex-col items-center justify-center py-24 space-y-4">
                <RefreshCw className="w-8 h-8 text-indigo-600 animate-spin" />
                <p className="text-sm text-slate-500 font-medium animate-pulse">{txt('Running AI deep-research & fetching client brief...', '正在进行 AI 背景调研并获取简介...')}</p>
              </div>
            ) : briefError ? (
              <div className="text-center py-20">
                <div className="inline-flex h-12 w-12 items-center justify-center rounded-full bg-slate-100 text-slate-400 mb-3">
                  <FileText className="w-6 h-6" />
                </div>
                <p className="text-sm text-slate-600 font-medium">{briefError}</p>
                <p className="text-xs text-slate-400 mt-2">{txt('AI will scan the website and generate a brief when you run a workflow.', '当运行工作流开发客户时，AI 会自动浏览其网站并生成此简介。')}</p>
                {selectedLead?.ai_draft && (
                  <div className="mt-8 text-left border-t border-slate-100 pt-6">
                    <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
                      <h3 className="text-sm font-semibold text-slate-900 flex items-center gap-2">
                        <MailIcon className="w-4 h-4 text-slate-400" /> {txt('Email draft generated:', '即使无简介，已生成邮件草稿:')}
                      </h3>
                      {selectedLead.email && (
                        <Button
                          size="sm"
                          onClick={() => sendReviewedDraft(selectedLead)}
                          disabled={sendingDraftId === selectedLead.id}
                          className="gap-2 bg-indigo-600 text-white hover:bg-indigo-700"
                        >
                          <MailIcon className="w-3.5 h-3.5" />
                          {sendingDraftId === selectedLead.id ? txt('Sending...', '发送中...') : txt('Send reviewed draft', '发送已审核草稿')}
                        </Button>
                      )}
                    </div>
                    <div className="relative group rounded-lg border border-slate-200 bg-slate-50 p-4 font-mono text-xs text-slate-800 whitespace-pre-wrap leading-relaxed">
                      {selectedLead.ai_draft}
                      <button
                        onClick={() => handleCopyDraft(selectedLead.ai_draft || '')}
                        className="absolute right-3 top-3 opacity-0 group-hover:opacity-100 transition-opacity bg-white border border-slate-200 hover:bg-slate-50 p-1.5 rounded text-slate-500 shadow-sm"
                        title={txt('Copy Draft', '复制草稿')}
                      >
                        {copiedDraft ? (
                          <Check className="w-4 h-4 text-emerald-500" />
                        ) : (
                          <Copy className="w-4 h-4" />
                        )}
                      </button>
                    </div>
                    {sendDraftMessage && (
                      <p className="mt-3 text-xs text-slate-500">{sendDraftMessage}</p>
                    )}
                  </div>
                )}
              </div>
            ) : leadBrief ? (
              <div className="grid gap-6 md:grid-cols-2">
                {/* Left Column: Research Brief Cards */}
                <div className="space-y-5">
                  <div className="rounded-xl border border-slate-100 bg-slate-50/50 p-5 shadow-sm">
                    <h3 className="text-sm font-bold text-slate-800 mb-3 flex items-center gap-2">
                      <Building className="w-4 h-4 text-indigo-600" /> {txt('Company Overview & Products', '公司概况与主营产品')}
                    </h3>
                    {leadBrief.company_overview ? (
                      <p className="text-sm text-slate-600 leading-relaxed whitespace-pre-wrap">{leadBrief.company_overview}</p>
                    ) : (
                      <p className="text-sm text-slate-400 italic">暂无公司概况</p>
                    )}
                    {leadBrief.specific_products && (
                      <div className="mt-4 pt-4 border-t border-slate-100/80">
                        <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider block mb-1.5">{txt('Extracted Products:', '提取出的主营产品:')}</span>
                        <div className="flex flex-wrap gap-1.5">
                          {leadBrief.specific_products.split(',').map((prod: string, i: number) => (
                            <span key={i} className="inline-flex items-center rounded-md bg-indigo-50 px-2 py-1 text-xs font-medium text-indigo-700 ring-1 ring-indigo-600/10">
                              {prod.trim()}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>

                  <div className="rounded-xl border border-slate-100 bg-slate-50/50 p-5 shadow-sm">
                    <h3 className="text-sm font-bold text-slate-800 mb-3 flex items-center gap-2">
                      <Zap className="w-4 h-4 text-amber-500" /> {txt('Recent Activity & Pain Points', '近期动态与痛点分析')}
                    </h3>
                    {leadBrief.recent_activity && (
                      <div className="mb-4">
                        <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider block mb-1">{txt('Recent Activity:', '近期动态:')}</span>
                        <p className="text-sm text-slate-600 leading-relaxed">{leadBrief.recent_activity}</p>
                      </div>
                    )}
                    {leadBrief.pain_points ? (
                      <div>
                        <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider block mb-1">{txt('Potential Pain Points:', '潜在痛点:')}</span>
                        <p className="text-sm text-slate-600 leading-relaxed whitespace-pre-wrap">{leadBrief.pain_points}</p>
                      </div>
                    ) : (
                      <p className="text-sm text-slate-400 italic">暂无痛点分析</p>
                    )}
                  </div>

                  <div className="rounded-xl border border-slate-100 bg-slate-50/50 p-5 shadow-sm">
                    <h3 className="text-sm font-bold text-slate-800 mb-3 flex items-center gap-2">
                      <Target className="w-4 h-4 text-emerald-600" /> {txt('Value Proposition & Hook', '价值主张匹配与破冰话术')}
                    </h3>
                    {leadBrief.value_proposition_alignment && (
                      <div className="mb-4">
                        <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider block mb-1">{txt('Value Prop Alignment:', '价值主张对齐:')}</span>
                        <p className="text-sm text-slate-600 leading-relaxed">{leadBrief.value_proposition_alignment}</p>
                      </div>
                    )}
                    {leadBrief.personalization_hook ? (
                      <div className="rounded-lg bg-indigo-50/40 border border-indigo-100/50 p-3">
                        <span className="text-xs font-semibold text-indigo-700 uppercase tracking-wider block mb-1">{txt('Personalization Hook:', '个性化破冰切入点:')}</span>
                        <p className="text-sm text-slate-700 font-medium leading-relaxed italic">&ldquo;{leadBrief.personalization_hook}&rdquo;</p>
                      </div>
                    ) : (
                      <p className="text-sm text-slate-400 italic">暂无个性化破冰点</p>
                    )}
                  </div>
                </div>

                {/* Right Column: AI Generated Email Draft */}
                <div className="flex flex-col h-full space-y-4">
                  <div className="flex-grow flex flex-col rounded-xl border border-slate-100 bg-slate-50/50 p-5 shadow-sm h-full">
                    <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
                      <h3 className="text-sm font-bold text-slate-800 flex items-center gap-2">
                        <MailIcon className="w-4 h-4 text-indigo-600" /> {txt('AI Outbound Email Draft', 'AI 智能发信草稿')}
                      </h3>
                      {selectedLead?.ai_draft && (
                        <div className="flex flex-wrap items-center gap-3">
                          <button
                            onClick={() => handleCopyDraft(selectedLead.ai_draft || '')}
                            className="flex items-center gap-1 text-xs text-indigo-600 hover:text-indigo-700 font-medium"
                          >
                            {copiedDraft ? (
                              <>
                                <Check className="w-3.5 h-3.5 text-emerald-500" /> {txt('Copied!', '已复制!')}
                              </>
                            ) : (
                              <>
                                <Copy className="w-3.5 h-3.5" /> {txt('Copy Draft', '复制草稿')}
                              </>
                            )}
                          </button>
                          {selectedLead.email && (
                            <Button
                              size="sm"
                              onClick={() => sendReviewedDraft(selectedLead)}
                              disabled={sendingDraftId === selectedLead.id}
                              className="h-8 gap-2 bg-indigo-600 text-white hover:bg-indigo-700"
                            >
                              <MailIcon className="w-3.5 h-3.5" />
                              {sendingDraftId === selectedLead.id ? txt('Sending...', '发送中...') : txt('Send reviewed draft', '发送已审核草稿')}
                            </Button>
                          )}
                        </div>
                      )}
                    </div>
                    
                    {selectedLead?.ai_draft ? (
                      <div className="flex-grow overflow-auto rounded-lg border border-slate-200 bg-white p-4 font-mono text-xs text-slate-800 whitespace-pre-wrap leading-relaxed shadow-inner max-h-[50vh]">
                        {selectedLead.ai_draft}
                      </div>
                    ) : (
                      <div className="flex-1 flex flex-col items-center justify-center text-center p-8 rounded-lg border border-dashed border-slate-200 bg-white">
                        <MailIcon className="w-8 h-8 text-slate-300 mb-2" />
                        <p className="text-xs text-slate-500 font-medium">{txt('No email draft generated for this lead', '尚未为该客户生成邮件草稿')}</p>
                        <p className="text-[11px] text-slate-400 mt-1">{txt('Please run a workflow, and the system will automatically generate personalized messages.', '请运行工作流，系统会自动评估并生成专属发信内容。')}</p>
                      </div>
                    )}
                    {sendDraftMessage && (
                      <p className="text-xs text-slate-500">{sendDraftMessage}</p>
                    )}
                  </div>
                </div>
              </div>
            ) : null}
          </div>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={deleteDialogOpen}
        title={txt('Confirm Delete', '确认删除')}
        message={txt('Are you sure you want to delete this client pool?', '确定要删除这个客户库吗？')}
        onConfirm={handleDeletePool}
        onCancel={() => { setDeleteDialogOpen(false); setDeleteTargetId(null); }}
      />
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
    case 'low_score':
      return 'bg-orange-100 text-orange-700 border border-orange-200';
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
    return d.toLocaleDateString(undefined, { year: '2-digit', month: '2-digit', day: '2-digit' });
  } catch {
    return '—';
  }
}

function formatRelative(iso: string) {
  try {
    const d = new Date(iso);
    const diff = (Date.now() - d.getTime()) / 1000;
    if (diff < 60) return `<1m`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h`;
    if (diff < 86400 * 7) return `${Math.floor(diff / 86400)}d`;
    return formatDate(iso);
  } catch {
    return '—';
  }
}

function ImportMetric({
  label,
  value,
  tone = 'slate',
}: {
  label: string
  value: number
  tone?: 'slate' | 'green' | 'amber' | 'red'
}) {
  const toneClasses = {
    slate: 'border-slate-200 bg-slate-50 text-slate-900',
    green: 'border-emerald-200 bg-emerald-50 text-emerald-700',
    amber: 'border-amber-200 bg-amber-50 text-amber-700',
    red: 'border-rose-200 bg-rose-50 text-rose-700',
  };
  return (
    <div className={`rounded-lg border p-3 ${toneClasses[tone]}`}>
      <div className="text-xs opacity-70">{label}</div>
      <div className="mt-1 text-2xl font-semibold">{value}</div>
    </div>
  );
}
