'use client';

import { FormEvent, useEffect, useMemo, useState } from 'react';
import { Building2, CheckCircle2, ChevronLeft, ChevronRight, ContactRound, ExternalLink, FileSearch, ListFilter, Search, ShieldCheck } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { mutateV2Json, v2Api } from '../api';
import type { Company, CompanyWorkspace } from '../types';
import { useV2Query } from '../use-v2-query';
import { EmptyState, LoadingState, ProductPageShell, QueryErrorState, SourceBanner } from '../components/product-ui';

const inputClass = 'mt-1 min-h-11 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm text-slate-950 outline-none focus-visible:border-indigo-500 focus-visible:ring-2 focus-visible:ring-indigo-200 disabled:bg-slate-100 disabled:text-slate-500';
const textareaClass = 'mt-1 min-h-20 w-full rounded-lg border border-slate-300 bg-white p-3 text-sm text-slate-950 outline-none focus-visible:border-indigo-500 focus-visible:ring-2 focus-visible:ring-indigo-200 disabled:bg-slate-100 disabled:text-slate-500';
const PAGE_SIZE = 25;

interface CompanyPayload {
  name: string;
  domain?: string;
  website?: string;
}

interface ContactPayload {
  company_id: number;
  full_name: string;
  job_title?: string;
  timezone?: string;
  contact_points: Array<{
    channel: 'email';
    value: string;
    verification_status: 'unverified';
    availability_status: 'available';
    is_primary: true;
  }>;
}

interface AudienceListPayload {
  name: string;
  description?: string;
}

function errorMessage(reason: unknown, fallback: string) {
  return reason instanceof Error ? reason.message : fallback;
}

function MutationError({ message }: { message: string | null }) {
  return message ? <p role="alert" className="mt-3 rounded-lg bg-rose-50 p-3 text-sm text-rose-800">{message}</p> : null;
}

function Pagination({ label, page, total, onPageChange }: { label: string; page: number; total: number; onPageChange: (page: number) => void }) {
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const start = total ? (page - 1) * PAGE_SIZE + 1 : 0;
  const end = Math.min(page * PAGE_SIZE, total);
  return (
    <nav aria-label={`${label} 分页`} className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 px-5 py-3">
      <p className="text-xs tabular-nums text-slate-500">当前 {start}–{end}，共 {total} 条</p>
      <div className="flex items-center gap-2">
        <Button type="button" variant="outline" size="sm" className="min-h-11" aria-label={`${label} 上一页`} disabled={page <= 1} onClick={() => onPageChange(page - 1)}><ChevronLeft className="h-4 w-4" />上一页</Button>
        <span className="min-w-24 text-center text-xs tabular-nums text-slate-600">第 {page} / {totalPages} 页</span>
        <Button type="button" variant="outline" size="sm" className="min-h-11" aria-label={`${label} 下一页`} disabled={page >= totalPages} onClick={() => onPageChange(page + 1)}>下一页<ChevronRight className="h-4 w-4" /></Button>
      </div>
    </nav>
  );
}

function ImpactPreview({
  title,
  effects,
  confirmLabel,
  pending,
  onCancel,
  onConfirm,
}: {
  title: string;
  effects: string[];
  confirmLabel: string;
  pending: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <section aria-label={`${title} · 影响确认`} className="mt-4 rounded-lg border border-amber-300 bg-amber-50 p-4">
      <h3 className="text-sm font-semibold text-amber-950">{title} · 影响确认</h3>
      <ul className="mt-2 list-disc space-y-1 pl-5 text-sm leading-6 text-amber-950">
        {effects.map(effect => <li key={effect}>{effect}</li>)}
      </ul>
      <div className="mt-3 flex flex-wrap justify-end gap-2">
        <Button type="button" variant="outline" className="min-h-11" disabled={pending} onClick={onCancel}>返回修改</Button>
        <Button type="button" className="min-h-11" disabled={pending} onClick={onConfirm}>
          <CheckCircle2 className="h-4 w-4" />{pending ? '创建中…' : confirmLabel}
        </Button>
      </div>
    </section>
  );
}

function CompanyCreateForm({ mutable, onCreated }: { mutable: boolean; onCreated: () => void }) {
  const [name, setName] = useState('');
  const [domain, setDomain] = useState('');
  const [website, setWebsite] = useState('');
  const [preview, setPreview] = useState<CompanyPayload | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const invalidatePreview = () => {
    setPreview(null);
    setError(null);
  };

  const preparePreview = (event: FormEvent) => {
    event.preventDefault();
    if (!mutable) return;
    const normalizedName = name.trim();
    const normalizedDomain = domain.trim();
    const normalizedWebsite = website.trim();
    if (!normalizedDomain && !normalizedWebsite) {
      setError('请至少填写标准域名或网站。');
      return;
    }
    setError(null);
    setPreview({
      name: normalizedName,
      ...(normalizedDomain ? { domain: normalizedDomain } : {}),
      ...(normalizedWebsite ? { website: normalizedWebsite } : {}),
    });
  };

  const confirm = async () => {
    if (!mutable || !preview) return;
    setPending(true);
    setError(null);
    try {
      await mutateV2Json<unknown, CompanyPayload>('/api/v2/companies', { method: 'POST', body: preview });
      toast.success(`Company「${preview.name}」已创建`);
      setName('');
      setDomain('');
      setWebsite('');
      setPreview(null);
      onCreated();
    } catch (reason) {
      const message = errorMessage(reason, 'Company 创建失败');
      setError(message);
      toast.error(message);
    } finally {
      setPending(false);
    }
  };

  return (
    <form onSubmit={preparePreview} className="border-b border-slate-200 bg-slate-50/70 p-5">
      <h3 className="text-sm font-semibold text-slate-950">创建 Company</h3>
      <p className="mt-1 text-xs leading-5 text-slate-600">只写入公司主数据，不会触发搜索、付费补全或外发。</p>
      <fieldset disabled={!mutable || pending} className="mt-3 grid gap-3 sm:grid-cols-3">
        <label className="text-xs font-semibold text-slate-700">
          公司名称
          <input aria-label="Company 名称" required maxLength={255} value={name} onChange={event => { setName(event.target.value); invalidatePreview(); }} className={inputClass} />
        </label>
        <label className="text-xs font-semibold text-slate-700">
          标准域名
          <input aria-label="Company 标准域名" maxLength={255} placeholder="example.com" value={domain} onChange={event => { setDomain(event.target.value); invalidatePreview(); }} className={inputClass} />
        </label>
        <label className="text-xs font-semibold text-slate-700">
          网站
          <input aria-label="Company 网站" type="url" maxLength={1000} placeholder="https://example.com" value={website} onChange={event => { setWebsite(event.target.value); invalidatePreview(); }} className={inputClass} />
        </label>
      </fieldset>
      <p className="mt-2 text-xs text-slate-500">标准域名与网站至少填写一项。</p>
      <Button type="submit" variant="outline" className="mt-3 min-h-11" disabled={!mutable || pending} title={!mutable ? '示例或混合数据不可写入' : undefined}>
        <ShieldCheck className="h-4 w-4" />预览创建 Company
      </Button>
      <MutationError message={error} />
      {preview ? (
        <ImpactPreview
          title="创建 Company"
          effects={[
            `创建公司主数据「${preview.name}」，域名或网站由后端规范化。`,
            '不创建 Contact、不加入 Campaign，不调用外部 Provider。',
          ]}
          confirmLabel="确认创建 Company"
          pending={pending}
          onCancel={() => setPreview(null)}
          onConfirm={confirm}
        />
      ) : null}
    </form>
  );
}

function ContactCreateForm({ companies, mutable, onCreated }: { companies: Company[]; mutable: boolean; onCreated: () => void }) {
  const [companyId, setCompanyId] = useState(companies[0]?.id ?? '');
  const [fullName, setFullName] = useState('');
  const [jobTitle, setJobTitle] = useState('');
  const [timezone, setTimezone] = useState('');
  const [email, setEmail] = useState('');
  const [preview, setPreview] = useState<ContactPayload | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!companies.some(company => company.id === companyId)) setCompanyId(companies[0]?.id ?? '');
  }, [companies, companyId]);

  const invalidatePreview = () => {
    setPreview(null);
    setError(null);
  };

  const preparePreview = (event: FormEvent) => {
    event.preventDefault();
    if (!mutable) return;
    const parsedCompanyId = Number(companyId);
    if (!Number.isInteger(parsedCompanyId) || parsedCompanyId <= 0) {
      setError('请选择已加载的 Company。');
      return;
    }
    const payload: ContactPayload = {
      company_id: parsedCompanyId,
      full_name: fullName.trim(),
      ...(jobTitle.trim() ? { job_title: jobTitle.trim() } : {}),
      ...(timezone.trim() ? { timezone: timezone.trim() } : {}),
      contact_points: [{
        channel: 'email',
        value: email.trim(),
        verification_status: 'unverified',
        availability_status: 'available',
        is_primary: true,
      }],
    };
    setError(null);
    setPreview(payload);
  };

  const confirm = async () => {
    if (!mutable || !preview) return;
    setPending(true);
    setError(null);
    try {
      await mutateV2Json<unknown, ContactPayload>('/api/v2/contacts', { method: 'POST', body: preview });
      toast.success(`Contact「${preview.full_name}」已创建，Email 仍为 unverified`);
      setFullName('');
      setJobTitle('');
      setTimezone('');
      setEmail('');
      setPreview(null);
      onCreated();
    } catch (reason) {
      const message = errorMessage(reason, 'Contact 创建失败');
      setError(message);
      toast.error(message);
    } finally {
      setPending(false);
    }
  };

  const companyName = companies.find(company => company.id === companyId)?.name ?? `Company #${companyId}`;
  const formDisabled = !mutable || !companies.length || pending;

  return (
    <form onSubmit={preparePreview} className="border-b border-slate-200 bg-slate-50/70 p-5">
      <h3 className="text-sm font-semibold text-slate-950">创建 Contact 与 Email ContactPoint</h3>
      <p className="mt-1 text-xs leading-5 text-slate-600">新 Email 始终以 <strong>unverified</strong> 写入；未经验证前不得外发。</p>
      <fieldset disabled={formDisabled} className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <label className="text-xs font-semibold text-slate-700">
          所属 Company
          <select aria-label="Contact 所属 Company" required value={companyId} onChange={event => { setCompanyId(event.target.value); invalidatePreview(); }} className={inputClass}>
            <option value="">选择 Company</option>
            {companies.map(company => <option key={company.id} value={company.id}>{company.name}</option>)}
          </select>
        </label>
        <label className="text-xs font-semibold text-slate-700">
          姓名
          <input aria-label="Contact 姓名" required maxLength={255} value={fullName} onChange={event => { setFullName(event.target.value); invalidatePreview(); }} className={inputClass} />
        </label>
        <label className="text-xs font-semibold text-slate-700">
          岗位
          <input aria-label="Contact 岗位" maxLength={255} value={jobTitle} onChange={event => { setJobTitle(event.target.value); invalidatePreview(); }} className={inputClass} />
        </label>
        <label className="text-xs font-semibold text-slate-700">
          Timezone
          <input aria-label="Contact timezone" maxLength={100} placeholder="Asia/Shanghai" value={timezone} onChange={event => { setTimezone(event.target.value); invalidatePreview(); }} className={inputClass} />
        </label>
        <label className="text-xs font-semibold text-slate-700">
          Email
          <input aria-label="Contact email" type="email" required maxLength={1000} value={email} onChange={event => { setEmail(event.target.value); invalidatePreview(); }} className={inputClass} />
        </label>
      </fieldset>
      {!companies.length ? <p className="mt-2 text-xs font-medium text-amber-800">请先创建并加载 Company，再创建 Contact。</p> : null}
      <Button type="submit" variant="outline" className="mt-3 min-h-11" disabled={formDisabled} title={!mutable ? '示例或混合数据不可写入' : undefined}>
        <ShieldCheck className="h-4 w-4" />预览创建 Contact
      </Button>
      <MutationError message={error} />
      {preview ? (
        <ImpactPreview
          title="创建 Contact"
          effects={[
            `在「${companyName}」下创建 Contact「${preview.full_name}」。`,
            `Email ${preview.contact_points[0].value} 保存为 unverified / available，不会伪称已验证。`,
            '不加入 Campaign，不触发验证、付费查询或发送。',
          ]}
          confirmLabel="确认创建 Contact"
          pending={pending}
          onCancel={() => setPreview(null)}
          onConfirm={confirm}
        />
      ) : null}
    </form>
  );
}

function AudienceListCreateForm({ mutable, onCreated }: { mutable: boolean; onCreated: () => void }) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [preview, setPreview] = useState<AudienceListPayload | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const invalidatePreview = () => {
    setPreview(null);
    setError(null);
  };

  const preparePreview = (event: FormEvent) => {
    event.preventDefault();
    if (!mutable) return;
    setError(null);
    setPreview({
      name: name.trim(),
      ...(description.trim() ? { description: description.trim() } : {}),
    });
  };

  const confirm = async () => {
    if (!mutable || !preview) return;
    setPending(true);
    setError(null);
    try {
      await mutateV2Json<unknown, AudienceListPayload>('/api/v2/lists', { method: 'POST', body: preview });
      toast.success(`Audience List「${preview.name}」已创建`);
      setName('');
      setDescription('');
      setPreview(null);
      onCreated();
    } catch (reason) {
      const message = errorMessage(reason, 'Audience List 创建失败');
      setError(message);
      toast.error(message);
    } finally {
      setPending(false);
    }
  };

  return (
    <form onSubmit={preparePreview} className="border-b border-slate-200 bg-slate-50/70 p-5">
      <h3 className="text-sm font-semibold text-slate-950">创建 Audience List</h3>
      <p className="mt-1 text-xs leading-5 text-slate-600">List 只负责分组，创建后不搜索、不入组 Campaign、不发送。</p>
      <fieldset disabled={!mutable || pending} className="mt-3 grid gap-3 sm:grid-cols-2">
        <label className="text-xs font-semibold text-slate-700">
          List 名称
          <input aria-label="Audience List 名称" required maxLength={255} value={name} onChange={event => { setName(event.target.value); invalidatePreview(); }} className={inputClass} />
        </label>
        <label className="text-xs font-semibold text-slate-700">
          说明（可选）
          <textarea aria-label="Audience List 说明" value={description} onChange={event => { setDescription(event.target.value); invalidatePreview(); }} className={textareaClass} />
        </label>
      </fieldset>
      <Button type="submit" variant="outline" className="mt-3 min-h-11" disabled={!mutable || pending} title={!mutable ? '示例或混合数据不可写入' : undefined}>
        <ShieldCheck className="h-4 w-4" />预览创建 Audience List
      </Button>
      <MutationError message={error} />
      {preview ? (
        <ImpactPreview
          title="创建 Audience List"
          effects={[
            `创建空分组「${preview.name}」，本次不添加成员。`,
            '不调用 Provider，不创建 Enrollment，不发送消息。',
          ]}
          confirmLabel="确认创建 Audience List"
          pending={pending}
          onCancel={() => setPreview(null)}
          onConfirm={confirm}
        />
      ) : null}
    </form>
  );
}

function evidenceText(workspace: CompanyWorkspace, key: string): string | null {
  for (const snapshot of workspace.evidence) {
    const value = snapshot.evidence[key];
    if (typeof value === 'string' && value.trim()) return value.trim();
    if (typeof value === 'number') return String(value);
  }
  return null;
}

function evidenceFlags(workspace: CompanyWorkspace): string[] {
  return Array.from(new Set(workspace.evidence.flatMap(snapshot => {
    const value = snapshot.evidence.quality_flags;
    return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string' && Boolean(item.trim())) : [];
  })));
}

function DossierField({ label, value }: { label: string; value: string | null }) {
  return (
    <article className="rounded-lg border border-slate-200 bg-white p-4">
      <h3 className="text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">{label}</h3>
      <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-700">{value || '暂无可靠证据；不会自动编造。'}</p>
    </article>
  );
}

function CompanyDossierDialog({
  companyId,
  mutable,
  open,
  onOpenChange,
  onChanged,
}: {
  companyId: string | null;
  mutable: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onChanged: () => void;
}) {
  const [workspace, setWorkspace] = useState<CompanyWorkspace | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [name, setName] = useState('');
  const [domain, setDomain] = useState('');
  const [industry, setIndustry] = useState('');
  const [region, setRegion] = useState('');
  const [editingContactId, setEditingContactId] = useState<string | null>(null);
  const [contactName, setContactName] = useState('');
  const [contactTitle, setContactTitle] = useState('');

  const loadWorkspace = async (signal?: AbortSignal) => {
    if (!companyId) return;
    setLoading(true);
    setError(null);
    try {
      const next = await v2Api.companyWorkspace(companyId, signal);
      setWorkspace(next);
      setName(next.company.name);
      setDomain(next.company.domain);
      setIndustry(next.company.industry === '未提供' ? '' : next.company.industry);
      setRegion(next.company.region === '未提供' ? '' : next.company.region);
    } catch (reason) {
      setError(errorMessage(reason, '客户档案加载失败'));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!open || !companyId) return;
    const controller = new AbortController();
    void loadWorkspace(controller.signal);
    return () => controller.abort();
  // loadWorkspace intentionally refreshes only when the selected dialog target changes.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, companyId]);

  const saveCompany = async () => {
    if (!workspace || !mutable) return;
    setSaving(true);
    setError(null);
    try {
      await v2Api.updateCompany(workspace.company.id, {
        name: name.trim(),
        domain: domain.trim() || null,
        industry: industry.trim() || null,
        region: region.trim() || null,
      });
      await loadWorkspace();
      onChanged();
      toast.success('公司主数据已更新');
    } catch (reason) {
      const message = errorMessage(reason, '公司资料更新失败');
      setError(message);
      toast.error(message);
    } finally {
      setSaving(false);
    }
  };

  const beginContactEdit = (contactId: string) => {
    const contact = workspace?.contacts.find(item => item.id === contactId);
    if (!contact) return;
    setEditingContactId(contact.id);
    setContactName(contact.name);
    setContactTitle(contact.title === '未提供' ? '' : contact.title);
  };

  const saveContact = async () => {
    if (!editingContactId || !mutable) return;
    setSaving(true);
    setError(null);
    try {
      await v2Api.updateContact(editingContactId, {
        full_name: contactName.trim(),
        job_title: contactTitle.trim() || null,
      });
      setEditingContactId(null);
      await loadWorkspace();
      onChanged();
      toast.success('联系人资料已更新');
    } catch (reason) {
      const message = errorMessage(reason, '联系人更新失败');
      setError(message);
      toast.error(message);
    } finally {
      setSaving(false);
    }
  };

  const flags = workspace ? evidenceFlags(workspace) : [];
  const fitScore = workspace ? evidenceText(workspace, 'fit_score') : null;
  const fitGrade = workspace ? evidenceText(workspace, 'fit_grade') : null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-5xl">
        <DialogHeader>
          <DialogTitle>{workspace?.company.name || '客户完整档案'}</DialogTitle>
          <DialogDescription>公司主数据、联系人、研究证据和历史触达集中显示；缺失字段明确标记，不会自动猜测。</DialogDescription>
        </DialogHeader>
        {loading && !workspace ? <LoadingState label="正在读取客户完整档案…" /> : null}
        {error ? <p role="alert" className="rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800">{error}</p> : null}
        {workspace ? (
          <div className="space-y-5">
            <section aria-label="客户关键指标" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
              {[
                ['匹配等级', fitGrade || '—'],
                ['匹配分', fitScore || '—'],
                ['联系人', workspace.contacts.length],
                ['已发送', workspace.outreach.sentCount],
                ['已回复', workspace.outreach.replyCount],
              ].map(([label, value]) => <article key={String(label)} className="rounded-lg border border-slate-200 bg-slate-50 p-3"><p className="text-xs text-slate-500">{label}</p><p className="mt-1 text-xl font-semibold tabular-nums text-slate-950">{value}</p></article>)}
            </section>

            <section aria-labelledby="company-master-data" className="rounded-lg border border-slate-200 bg-slate-50 p-4">
              <h2 id="company-master-data" className="text-sm font-semibold text-slate-950">公司主数据</h2>
              <div className="mt-3 grid gap-3 sm:grid-cols-2">
                <label className="text-xs font-semibold text-slate-700">公司名称<input aria-label="编辑公司名称" value={name} onChange={event => setName(event.target.value)} disabled={!mutable || saving} className={inputClass} /></label>
                <label className="text-xs font-semibold text-slate-700">标准域名<input aria-label="编辑公司域名" value={domain} onChange={event => setDomain(event.target.value)} disabled={!mutable || saving} className={inputClass} /></label>
                <label className="text-xs font-semibold text-slate-700">行业<input aria-label="编辑公司行业" value={industry} onChange={event => setIndustry(event.target.value)} disabled={!mutable || saving} className={inputClass} /></label>
                <label className="text-xs font-semibold text-slate-700">地区<input aria-label="编辑公司地区" value={region} onChange={event => setRegion(event.target.value)} disabled={!mutable || saving} className={inputClass} /></label>
              </div>
              <Button type="button" className="mt-3 min-h-11" disabled={!mutable || saving || !name.trim()} onClick={saveCompany}>{saving ? '保存中…' : '保存公司资料'}</Button>
            </section>

            <section aria-labelledby="research-dossier">
              <div className="flex flex-wrap items-center gap-2">
                <FileSearch className="h-4 w-4 text-indigo-700" />
                <h2 id="research-dossier" className="text-sm font-semibold text-slate-950">研究档案</h2>
                <span className="text-xs text-slate-500">{workspace.evidence.length} 条证据快照</span>
              </div>
              <div className="mt-3 grid gap-3 lg:grid-cols-2">
                <DossierField label="公司概况" value={evidenceText(workspace, 'company_overview')} />
                <DossierField label="具体产品" value={evidenceText(workspace, 'specific_products')} />
                <DossierField label="近期新闻" value={evidenceText(workspace, 'recent_news')} />
                <DossierField label="近期活动" value={evidenceText(workspace, 'recent_activity')} />
                <DossierField label="潜在痛点" value={evidenceText(workspace, 'pain_points')} />
                <DossierField label="价值匹配" value={evidenceText(workspace, 'value_proposition_alignment')} />
                <div className="lg:col-span-2"><DossierField label="个性化切入点" value={evidenceText(workspace, 'personalization_hook')} /></div>
              </div>
              {flags.length ? <div className="mt-3 flex flex-wrap gap-2" aria-label="研究质量标记">{flags.map(flag => <span key={flag} className="rounded-full border border-slate-200 bg-white px-2.5 py-1 text-xs text-slate-700">{flag}</span>)}</div> : null}
            </section>

            <section aria-labelledby="workspace-contacts" className="rounded-lg border border-slate-200 bg-white">
              <h2 id="workspace-contacts" className="border-b border-slate-200 p-4 text-sm font-semibold text-slate-950">联系人与联系点</h2>
              {workspace.contacts.length ? <ul className="divide-y divide-slate-200">{workspace.contacts.map(contact => (
                <li key={contact.id} className="p-4">
                  {editingContactId === contact.id ? (
                    <div className="grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
                      <label className="text-xs font-semibold text-slate-700">姓名<input aria-label={`编辑联系人姓名 ${contact.name}`} value={contactName} onChange={event => setContactName(event.target.value)} className={inputClass} /></label>
                      <label className="text-xs font-semibold text-slate-700">岗位<input aria-label={`编辑联系人岗位 ${contact.name}`} value={contactTitle} onChange={event => setContactTitle(event.target.value)} className={inputClass} /></label>
                      <div className="flex gap-2"><Button type="button" disabled={saving || !contactName.trim()} onClick={saveContact}>保存</Button><Button type="button" variant="outline" disabled={saving} onClick={() => setEditingContactId(null)}>取消</Button></div>
                    </div>
                  ) : (
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                      <div><p className="font-medium text-slate-950">{contact.name}</p><p className="mt-1 text-xs text-slate-500">{contact.title} · {contact.email}</p></div>
                      <div className="flex flex-wrap items-center gap-2"><span className={`rounded-full border px-2.5 py-1 text-xs font-semibold ${contact.verified ? 'border-emerald-200 bg-emerald-50 text-emerald-800' : 'border-amber-200 bg-amber-50 text-amber-900'}`}>{contact.status}</span><Button type="button" variant="outline" size="sm" disabled={!mutable} onClick={() => beginContactEdit(contact.id)}>编辑</Button></div>
                    </div>
                  )}
                </li>
              ))}</ul> : <div className="p-4"><EmptyState title="暂无可靠联系人" detail="可创建 Contact；新邮箱必须经过独立验证后才能发送。" /></div>}
            </section>

            <section aria-labelledby="evidence-sources" className="rounded-lg border border-slate-200 bg-slate-50 p-4">
              <h2 id="evidence-sources" className="text-sm font-semibold text-slate-950">证据来源</h2>
              {workspace.evidence.length ? <ul className="mt-3 space-y-2">{workspace.evidence.map(snapshot => <li key={snapshot.id} className="flex flex-col gap-1 rounded-md bg-white p-3 text-xs text-slate-600 sm:flex-row sm:items-center sm:justify-between"><span>{snapshot.source} · 置信度 {(snapshot.confidence * 100).toFixed(0)}% · {new Date(snapshot.capturedAt).toLocaleString('zh-CN', { hour12: false })}</span>{snapshot.sourceUrl ? <a href={snapshot.sourceUrl} target="_blank" rel="noreferrer" className="inline-flex min-h-11 items-center gap-1 py-3 font-semibold text-indigo-700 hover:underline">打开来源<ExternalLink className="h-3 w-3" /></a> : null}</li>)}</ul> : <p className="mt-2 text-sm text-slate-600">暂无证据来源，客户不可进入自动外发。</p>}
            </section>
          </div>
        ) : null}
        <DialogFooter><Button type="button" variant="outline" onClick={() => onOpenChange(false)}>关闭</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function CustomersPage() {
  const { result, loading, error, refresh } = useV2Query(v2Api.customers);
  const mutable = result?.source === 'live';
  const [searchQuery, setSearchQuery] = useState('');
  const [emailFilter, setEmailFilter] = useState<'all' | 'verified' | 'unverified' | 'missing'>('all');
  const [selectedCompanyId, setSelectedCompanyId] = useState<string | null>(null);
  const [createDialog, setCreateDialog] = useState<'company' | 'contact' | 'list' | null>(null);
  const [companyPage, setCompanyPage] = useState(1);
  const [contactPage, setContactPage] = useState(1);
  const filteredCompanies = useMemo(() => {
    if (!result) return [];
    const query = searchQuery.trim().toLocaleLowerCase();
    return result.data.companies.filter(company => {
      const contacts = result.data.contacts.filter(contact => contact.companyId === company.id);
      const matchesQuery = !query || [
        company.name,
        company.domain,
        company.industry,
        company.region,
        ...contacts.flatMap(contact => [contact.name, contact.title, contact.email]),
      ].some(value => value.toLocaleLowerCase().includes(query));
      const matchesEmail = emailFilter === 'all'
        || (emailFilter === 'verified' && contacts.some(contact => contact.verified))
        || (emailFilter === 'unverified' && contacts.some(contact => contact.email !== '未提供' && !contact.verified))
        || (emailFilter === 'missing' && (!contacts.length || contacts.every(contact => contact.email === '未提供')));
      return matchesQuery && matchesEmail;
    });
  }, [emailFilter, result, searchQuery]);
  const filteredContacts = useMemo(() => {
    if (!result) return [];
    const query = searchQuery.trim().toLocaleLowerCase();
    return result.data.contacts.filter(contact => {
      const matchesQuery = !query || [
        contact.name,
        contact.company,
        contact.domain,
        contact.title,
        contact.email,
      ].some(value => value.toLocaleLowerCase().includes(query));
      const matchesEmail = emailFilter === 'all'
        || (emailFilter === 'verified' && contact.verified)
        || (emailFilter === 'unverified' && contact.email !== '未提供' && !contact.verified)
        || (emailFilter === 'missing' && contact.email === '未提供');
      return matchesQuery && matchesEmail;
    });
  }, [emailFilter, result, searchQuery]);
  useEffect(() => {
    setCompanyPage(1);
    setContactPage(1);
  }, [emailFilter, searchQuery]);
  useEffect(() => {
    setCompanyPage(page => Math.min(page, Math.max(1, Math.ceil(filteredCompanies.length / PAGE_SIZE))));
  }, [filteredCompanies.length]);
  useEffect(() => {
    setContactPage(page => Math.min(page, Math.max(1, Math.ceil(filteredContacts.length / PAGE_SIZE))));
  }, [filteredContacts.length]);
  const pagedCompanies = useMemo(
    () => filteredCompanies.slice((companyPage - 1) * PAGE_SIZE, companyPage * PAGE_SIZE),
    [companyPage, filteredCompanies],
  );
  const pagedContacts = useMemo(
    () => filteredContacts.slice((contactPage - 1) * PAGE_SIZE, contactPage * PAGE_SIZE),
    [contactPage, filteredContacts],
  );
  return (
    <ProductPageShell eyebrow="客户主数据" title="客户" description="集中查看公司、联系人、邮箱验证状态和来源证据；未验证记录可以保存，但不能发送。">
      {error ? <QueryErrorState error={error} onRetry={refresh} /> : loading || !result ? <LoadingState label="正在读取 Company、Contact 与 List…" /> : (
        <>
          <SourceBanner envelope={result} onRefresh={refresh} />
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-200 bg-white p-4"><p className="text-sm text-slate-600">批量准备客户请使用“找客户”；这里只维护单条主数据。</p><div className="flex flex-wrap gap-2"><Button type="button" variant="outline" disabled={!mutable} onClick={() => setCreateDialog('company')}>新建公司</Button><Button type="button" variant="outline" disabled={!mutable || !result.data.companies.length} onClick={() => setCreateDialog('contact')}>新建联系人</Button><Button type="button" variant="outline" disabled={!mutable} onClick={() => setCreateDialog('list')}>新建分组</Button></div></div>
          {!mutable ? (
            <p role="status" aria-label="客户库写入状态" className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm font-medium text-amber-950">
              当前是{result.source === 'mixed' ? '混合' : '示例'}数据：所有创建入口已锁定，只有完整的 V2 实时数据可写入。
            </p>
          ) : null}
          <section aria-label="客户库筛选" className="grid gap-3 rounded-lg border border-slate-200 bg-white p-4 shadow-sm sm:grid-cols-[1fr_220px]">
            <label className="relative text-xs font-semibold text-slate-700">
              搜索客户
              <Search className="pointer-events-none absolute bottom-3 left-3 h-4 w-4 text-slate-400" />
              <input aria-label="搜索公司、域名、联系人或邮箱" value={searchQuery} onChange={event => setSearchQuery(event.target.value)} placeholder="公司、域名、联系人、岗位、邮箱……" className={`${inputClass} pl-9`} />
            </label>
            <label className="text-xs font-semibold text-slate-700">
              邮箱状态
              <select aria-label="筛选邮箱状态" value={emailFilter} onChange={event => setEmailFilter(event.target.value as typeof emailFilter)} className={inputClass}>
                <option value="all">全部邮箱状态</option>
                <option value="verified">至少一个已验证邮箱</option>
                <option value="unverified">有邮箱但未验证</option>
                <option value="missing">无邮箱</option>
              </select>
            </label>
          </section>
          <section aria-labelledby="companies-heading" className="rounded-lg border border-slate-200 bg-white shadow-sm">
            <div className="flex items-center gap-2 border-b border-slate-200 p-5"><Building2 className="h-5 w-5 text-indigo-700" /><h2 id="companies-heading" className="font-semibold text-slate-950">Companies</h2><span className="ml-auto text-xs text-slate-500">匹配 {filteredCompanies.length} / {result.data.companies.length} 家</span></div>
            {filteredCompanies.length ? (
              <div
                className="overflow-x-auto focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600"
                role="region"
                aria-label="Companies 表格，可横向滚动"
                tabIndex={0}
              >
                <table className="w-full min-w-[720px] text-left text-sm">
                  <thead><tr className="text-xs uppercase tracking-wide text-slate-500"><th className="px-5 py-3">公司</th><th className="px-5 py-3">行业 / 地区</th><th className="px-5 py-3">联系人</th><th className="px-5 py-3">已验证邮箱</th><th className="px-5 py-3">操作</th></tr></thead>
                  <tbody className="divide-y divide-slate-200">{pagedCompanies.map(company => <tr key={company.id}><td className="px-5 py-4"><div className="font-medium text-slate-950">{company.name}</div><div className="mt-1 text-xs text-slate-500">{company.domain || '无标准化域名'}</div></td><td className="px-5 py-4 text-slate-600">{company.industry}<span className="mx-2 text-slate-300">·</span>{company.region}</td><td className="px-5 py-4 tabular-nums text-slate-700">{company.contacts}</td><td className="px-5 py-4 tabular-nums text-slate-700">{company.verifiedContacts}</td><td className="px-5 py-4"><Button type="button" variant="outline" size="sm" className="min-h-11" disabled={!mutable} title={!mutable ? '仅 V2 实时数据可读取完整档案' : undefined} onClick={() => setSelectedCompanyId(company.id)}>查看完整档案</Button></td></tr>)}</tbody>
                </table>
                <Pagination label="Companies" page={companyPage} total={filteredCompanies.length} onPageChange={setCompanyPage} />
              </div>
            ) : <div className="p-5"><EmptyState title={result.data.companies.length ? '没有匹配的客户' : '尚无 Company'} detail={result.data.companies.length ? '调整搜索词或邮箱筛选条件。' : '使用上方 V2 创建入口建立公司主数据。'} /></div>}
          </section>
          <section aria-labelledby="contacts-heading" className="rounded-lg border border-slate-200 bg-white shadow-sm">
            <div className="flex items-center gap-2 border-b border-slate-200 p-5"><ContactRound className="h-5 w-5 text-indigo-700" /><h2 id="contacts-heading" className="font-semibold text-slate-950">Contacts & ContactPoints</h2><span className="ml-auto text-xs text-slate-500">匹配 {filteredContacts.length} / {result.data.contacts.length} 人</span></div>
            {filteredContacts.length ? (
              <div
                className="overflow-x-auto focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600"
                role="region"
                aria-label="Contacts 与 ContactPoints 表格，可横向滚动"
                tabIndex={0}
              >
                <table className="w-full min-w-[820px] text-left text-sm">
                  <thead><tr className="text-xs uppercase tracking-wide text-slate-500"><th className="px-5 py-3">联系人</th><th className="px-5 py-3">公司</th><th className="px-5 py-3">联系点</th><th className="px-5 py-3">验证 / 可用</th></tr></thead>
                  <tbody className="divide-y divide-slate-200">{pagedContacts.map(contact => <tr key={contact.id}><td className="px-5 py-4"><div className="font-medium text-slate-950">{contact.name}</div><div className="mt-1 text-xs text-slate-500">{contact.title}</div></td><td className="px-5 py-4 text-slate-600">{contact.company}</td><td className="px-5 py-4"><div className="text-slate-800">{contact.email}</div><div className="mt-1 text-xs text-slate-500">{contact.channels.join(' · ') || '无渠道'}</div></td><td className="px-5 py-4"><span className={`rounded-full border px-2.5 py-1 text-xs font-semibold ${contact.verified ? 'border-emerald-200 bg-emerald-50 text-emerald-800' : 'border-amber-200 bg-amber-50 text-amber-900'}`}>{contact.status}</span></td></tr>)}</tbody>
                </table>
                <Pagination label="Contacts" page={contactPage} total={filteredContacts.length} onPageChange={setContactPage} />
              </div>
            ) : <div className="p-5"><EmptyState title={result.data.contacts.length ? '没有匹配的联系人' : '尚无 Contact'} detail={result.data.contacts.length ? '调整搜索词或邮箱筛选条件。' : '无法确定身份或 owner 的记录会进入 quarantine，不会自动猜测。'} /></div>}
          </section>
          <section aria-labelledby="lists-heading" className="rounded-lg border border-slate-200 bg-white shadow-sm">
            <div className="flex items-center gap-2 border-b border-slate-200 p-5"><ListFilter className="h-5 w-5 text-indigo-700" /><h2 id="lists-heading" className="font-semibold text-slate-950">Audience Lists</h2></div>
            <div className="p-5">
              <p className="text-xs text-slate-500">List 只分组，不直接触发搜索或发送。</p>
              {result.data.lists.length ? (
                <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{result.data.lists.map(list => <article key={list.id} className="rounded-lg border border-slate-200 bg-slate-50 p-4"><h3 className="text-sm font-semibold text-slate-950">{list.name}</h3><p className="mt-1 text-xs leading-5 text-slate-600">{list.description || '无说明'}</p><p className="mt-3 text-xs text-slate-500">成员：{list.total ?? '接口未汇总'}</p></article>)}</div>
              ) : <div className="mt-4"><EmptyState title="尚无 Audience List" detail="创建 List 后再单独管理成员；创建动作本身不触发 Campaign。" /></div>}
            </div>
          </section>
          <CompanyDossierDialog
            companyId={selectedCompanyId}
            mutable={mutable}
            open={selectedCompanyId !== null}
            onOpenChange={open => { if (!open) setSelectedCompanyId(null); }}
            onChanged={refresh}
          />
          <Dialog open={createDialog !== null} onOpenChange={open => { if (!open) setCreateDialog(null); }}>
            <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
              <DialogHeader><DialogTitle>{createDialog === 'company' ? '新建公司' : createDialog === 'contact' ? '新建联系人' : '新建客户分组'}</DialogTitle><DialogDescription>表单提交前会显示影响预览；邮箱仍需独立验证才能用于发送。</DialogDescription></DialogHeader>
              {createDialog === 'company' ? <CompanyCreateForm mutable={mutable} onCreated={() => { refresh(); setCreateDialog(null); }} /> : null}
              {createDialog === 'contact' ? <ContactCreateForm companies={result.data.companies} mutable={mutable} onCreated={() => { refresh(); setCreateDialog(null); }} /> : null}
              {createDialog === 'list' ? <AudienceListCreateForm mutable={mutable} onCreated={() => { refresh(); setCreateDialog(null); }} /> : null}
            </DialogContent>
          </Dialog>
        </>
      )}
    </ProductPageShell>
  );
}
