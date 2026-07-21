'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import {
  AlertTriangle,
  ArrowRight,
  Check,
  CheckCircle2,
  FileUp,
  LoaderCircle,
  Search,
  Send,
  ShieldCheck,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { cn } from '@/lib/utils';
import {
  type AcquisitionRunRead,
  type ActivationLaunchDraft,
  type ActivationLaunchPreview,
  type ActivationRead,
  V2MutationError,
  v2Api,
} from '../api';
import type { ChannelAccount } from '../types';
import { ProductPageShell } from '../components/product-ui';

type Candidate = NonNullable<AcquisitionRunRead['candidates']>[number];
type SourceMode = 'csv' | 'ai';

const inputClass = 'min-h-11 border-slate-300 bg-white text-slate-950';

function splitTerms(value: string) {
  return value.split(/[,;\n，；]+/).map(item => item.trim()).filter(Boolean);
}

function errorMessage(error: unknown) {
  if (error instanceof V2MutationError) return error.message;
  if (error instanceof Error) return error.message;
  return '操作失败，请重试。';
}

function candidateLabel(candidate: Candidate) {
  return candidate.full_name
    ?? [candidate.first_name, candidate.last_name].filter(Boolean).join(' ')
    ?? candidate.email
    ?? `候选 #${candidate.id}`;
}

function statusLabel(candidate: Candidate) {
  if (candidate.status === 'committed') return '已入库';
  if (candidate.status === 'duplicate') return '重复';
  if (candidate.status === 'invalid') return '已拦截';
  if (candidate.verification_status === 'valid') return '验证有效';
  if (candidate.status === 'selected') return '已选中';
  return '待验证';
}

function safeHttpUrl(value: string | null) {
  if (!value) return undefined;
  try {
    const parsed = new URL(value);
    return parsed.protocol === 'https:' || parsed.protocol === 'http:' ? parsed.href : undefined;
  } catch {
    return undefined;
  }
}

async function waitForRun(runId: number, terminal: Set<string>) {
  let latest = await v2Api.acquisitionRun(runId);
  for (let attempt = 0; attempt < 24 && !terminal.has(latest.status); attempt += 1) {
    await new Promise(resolve => window.setTimeout(resolve, 750));
    latest = await v2Api.acquisitionRun(runId);
  }
  return latest;
}

export default function ActivationPage({ acquisitionOnly = false }: { acquisitionOnly?: boolean }) {
  const [activation, setActivation] = useState<ActivationRead>();
  const [accounts, setAccounts] = useState<ChannelAccount[]>([]);
  const [run, setRun] = useState<AcquisitionRunRead>();
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [sourceMode, setSourceMode] = useState<SourceMode>('csv');
  const [file, setFile] = useState<File>();
  const [paidConfirmed, setPaidConfirmed] = useState(false);
  const [commitConfirmed, setCommitConfirmed] = useState(false);
  const [launchConfirmed, setLaunchConfirmed] = useState(false);
  const [busy, setBusy] = useState<string>();
  const [message, setMessage] = useState<string>();
  const [error, setError] = useState<string>();
  const [launchPreview, setLaunchPreview] = useState<ActivationLaunchPreview>();
  const [launchJobId, setLaunchJobId] = useState<number>();
  const [searchDraft, setSearchDraft] = useState({
    name: '首批目标客户',
    productSummary: '',
    industries: '',
    roles: '',
    regions: '',
    limit: 10,
  });
  const [launchDraft, setLaunchDraft] = useState({
    planName: '首批客户试跑',
    objective: '验证目标客户是否愿意进一步了解我们的产品',
    tone: '专业、简洁、尊重',
    language: '中文',
    subject: '{{company_name}} 的业务合作建议',
    body: '你好 {{first_name}}，\n\n我注意到 {{company_name}} 的业务与我们的服务高度匹配，想与你分享一个简短建议。\n\n{{unsubscribe_url}}',
    dailyLimit: 10,
  });

  const refresh = useCallback(async () => {
    setError(undefined);
    try {
      const [snapshot, channelAccounts] = await Promise.all([
        v2Api.activation(),
        v2Api.channelAccounts(),
      ]);
      setActivation(snapshot);
      setAccounts(channelAccounts.filter(account => account.channel === 'email'));
      if (snapshot.latest_run_id) {
        const latest = await v2Api.acquisitionRun(snapshot.latest_run_id);
        setRun(latest);
        setSelectedIds(
          (latest.candidates ?? [])
            .filter(candidate => candidate.selected || candidate.status === 'committed')
            .slice(0, 20)
            .map(candidate => candidate.id),
        );
      }
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  useEffect(() => {
    if (acquisitionOnly || activation?.activated || !activation?.campaign_id) return;
    const timer = window.setInterval(() => { void refresh(); }, 3_000);
    return () => window.clearInterval(timer);
  }, [acquisitionOnly, activation?.activated, activation?.campaign_id, refresh]);

  const candidates = run?.candidates ?? [];
  const selectable = candidates.filter(candidate => candidate.status === 'ready' || candidate.status === 'selected');
  const verifiedIds = candidates
    .filter(candidate => candidate.selected && candidate.verification_status === 'valid')
    .map(candidate => candidate.id);
  const committedIds = candidates
    .filter(candidate => candidate.status === 'committed' && candidate.committed_contact_point_id)
    .slice(0, 20)
    .map(candidate => candidate.id);
  const healthyAccounts = accounts.filter(account => (
    account.enabled
    && account.healthStatus === 'healthy'
    && (account.provider.startsWith('fake-') || account.credentialsConfigured)
  ));
  const selectedAccount = healthyAccounts[0];
  const shownCandidates = candidates.slice(0, 100);
  const currentStep = acquisitionOnly ? 3 : (activation?.current_step ?? 1);

  const activationSteps = activation?.steps ?? [];
  const stepHref = (key: string) => activationSteps.find(step => step.key === key)?.href ?? '#';

  const runAction = async (name: string, action: () => Promise<void>) => {
    setBusy(name);
    setError(undefined);
    setMessage(undefined);
    try {
      await action();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(undefined);
    }
  };

  const importCsv = () => runAction('import', async () => {
    if (!file) throw new Error('请先选择 CSV 文件。');
    const imported = await v2Api.importAcquisitionCsv(file, file.name.replace(/\.csv$/i, '') || 'CSV 首批客户');
    setRun(imported);
    setSelectedIds((imported.candidates ?? []).filter(candidate => candidate.status === 'ready').slice(0, 20).map(candidate => candidate.id));
    setMessage(`已安全预览 ${imported.candidates?.length ?? 0} 条记录；尚未写入正式客户库。`);
  });

  const searchCustomers = () => runAction('search', async () => {
    if (!paidConfirmed) throw new Error('请先确认本次付费查找。');
    const started = await v2Api.searchAcquisition({
      name: searchDraft.name,
      product_summary: searchDraft.productSummary,
      target_industries: splitTerms(searchDraft.industries),
      target_roles: splitTerms(searchDraft.roles),
      target_regions: splitTerms(searchDraft.regions),
      limit: searchDraft.limit,
      paid_action_confirmed: true,
    });
    setRun(started);
    const finished = await waitForRun(started.id, new Set(['ready', 'failed']));
    setRun(finished);
    setSelectedIds([]);
    setPaidConfirmed(false);
    if (finished.status === 'failed') throw new Error(finished.last_error ?? '找客户任务失败。');
    if (finished.status !== 'ready') throw new Error('找客户任务仍在处理，请稍后刷新状态。');
    setMessage(`已找到 ${finished.candidates?.length ?? 0} 个可审核候选，尚未购买邮箱或写入客户库。`);
  });

  const verifySelected = () => runAction('verify', async () => {
    const ids = selectedIds.filter(id => selectable.some(candidate => candidate.id === id));
    if (!ids.length) throw new Error('请选择 1–20 个待验证候选。');
    if (!paidConfirmed) throw new Error('验证邮箱可能产生 Provider 费用，请先确认。');
    const started = await v2Api.verifyAcquisition(run!.id, {
      candidate_ids: ids,
      paid_action_confirmed: true,
    });
    const finished = await waitForRun(started.id, new Set(['verified', 'failed']));
    setRun(finished);
    setSelectedIds((finished.candidates ?? []).filter(candidate => candidate.selected).map(candidate => candidate.id));
    if (finished.status === 'failed') throw new Error(finished.last_error ?? '邮箱验证失败。');
    if (finished.status !== 'verified') throw new Error('邮箱验证仍在处理，请稍后刷新状态。');
    setMessage('邮箱验证已完成；只有明确有效的工作邮箱可进入客户库。');
  });

  const commitVerified = () => runAction('commit', async () => {
    if (!verifiedIds.length) throw new Error('当前没有已验证有效的候选。');
    if (!commitConfirmed) throw new Error('请确认所选客户证据后再入库。');
    const committed = await v2Api.commitAcquisition(run!.id, {
      candidate_ids: verifiedIds.slice(0, 20),
      human_confirmed: true,
    });
    setRun(committed);
    setSelectedIds((committed.candidates ?? []).filter(candidate => candidate.status === 'committed').map(candidate => candidate.id));
    setMessage(`已将 ${verifiedIds.length} 位已验证联系人写入正式客户库。`);
    await refresh();
  });

  const launchPayload = useMemo<ActivationLaunchDraft | undefined>(() => {
    if (!run || !selectedAccount || !committedIds.length) return undefined;
    return {
      run_id: run.id,
      candidate_ids: committedIds,
      channel_account_id: selectedAccount.id,
      plan_name: launchDraft.planName,
      objective: launchDraft.objective,
      tone: launchDraft.tone,
      language: launchDraft.language,
      subject_template: launchDraft.subject,
      body_template: launchDraft.body,
      daily_limit: Math.min(launchDraft.dailyLimit, committedIds.length, 20),
    };
  }, [committedIds, launchDraft, run, selectedAccount]);

  const previewLaunch = () => runAction('preview-launch', async () => {
    if (!launchPayload) throw new Error('需要已入库客户和健康的发件邮箱。');
    const preview = await v2Api.previewActivationLaunch(launchPayload);
    setLaunchPreview(preview);
    setLaunchConfirmed(false);
    setMessage(preview.blockers.length ? '预检发现阻断，请先处理。' : '预检通过；确认后只会创建逐封审核计划。');
  });

  const launch = () => runAction('launch', async () => {
    if (!launchPayload || !launchPreview) throw new Error('请先生成最新启动预检。');
    if (launchPreview.blockers.length) throw new Error(launchPreview.blockers[0]);
    if (!launchConfirmed) throw new Error('请确认将创建逐封审核计划。');
    const job = await v2Api.launchActivation({
      ...launchPayload,
      preview_checksum: launchPreview.checksum,
      human_confirmed: true,
    });
    setLaunchJobId(job.job_id);
    setMessage('触达计划已入队；所有邮件仍需逐封人工审核。');
    await refresh();
  });

  return (
    <ProductPageShell
      eyebrow="Product V2 · 首次启用"
      title={acquisitionOnly ? '找客户' : '完成第一次安全触达'}
      description="候选资料与正式客户库隔离；付费动作、客户入库和计划启动都需要单独确认。"
    >
      {error ? <div role="alert" className="rounded-lg border border-rose-300 bg-rose-50 px-4 py-3 text-sm text-rose-950"><strong>未执行：</strong>{error}</div> : null}
      {message ? <div role="status" className="rounded-lg border border-emerald-300 bg-emerald-50 px-4 py-3 text-sm text-emerald-950">{message}</div> : null}

      {!acquisitionOnly ? (
        <ol aria-label="首次启用进度" className="grid gap-3 sm:grid-cols-5">
          {(activationSteps.length ? activationSteps : [
            { key: 'icp', label: '定义 ICP', completed: false, detail: '' },
            { key: 'mailbox', label: '发件邮箱', completed: false, detail: '' },
            { key: 'customers', label: '首批客户', completed: false, detail: '' },
            { key: 'plan', label: '审核计划', completed: false, detail: '' },
            { key: 'send', label: '首封邮件', completed: false, detail: '' },
          ]).map((step, index) => (
            <li key={step.key} aria-current={currentStep === index + 1 ? 'step' : undefined} className={cn('rounded-lg border p-3', step.completed ? 'border-emerald-200 bg-emerald-50' : currentStep === index + 1 ? 'border-indigo-300 bg-indigo-50' : 'border-slate-200 bg-white')}>
              <div className="flex items-center gap-2 text-xs font-semibold text-slate-700">{step.completed ? <CheckCircle2 className="h-4 w-4 text-emerald-700" /> : <span className="flex h-5 w-5 items-center justify-center rounded-full bg-slate-200 text-[11px]">{index + 1}</span>}{step.label}</div>
            </li>
          ))}
        </ol>
      ) : null}

      {!acquisitionOnly ? (
        <section aria-labelledby="setup-heading" className="grid gap-4 md:grid-cols-2">
          <article className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm"><h2 id="setup-heading" className="font-semibold text-slate-950">1. 发布 ICP / Playbook</h2><p className="mt-2 text-sm leading-6 text-slate-600">{activationSteps.find(step => step.key === 'icp')?.detail ?? '填写产品、行业、角色和证据要求。'}</p><Link href={stepHref('icp')} className="mt-4 inline-flex min-h-11 items-center text-sm font-semibold text-indigo-700">打开 ICP 设置<ArrowRight className="ml-2 h-4 w-4" /></Link></article>
          <article className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm"><h2 className="font-semibold text-slate-950">2. 确认发件邮箱</h2><p className="mt-2 text-sm leading-6 text-slate-600">{activationSteps.find(step => step.key === 'mailbox')?.detail ?? '只能使用已绑定、凭据完整且健康的邮箱。'}</p><Link href={stepHref('mailbox')} className="mt-4 inline-flex min-h-11 items-center text-sm font-semibold text-indigo-700">打开渠道设置<ArrowRight className="ml-2 h-4 w-4" /></Link></article>
        </section>
      ) : null}

      <section aria-labelledby="source-heading" className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between"><div><h2 id="source-heading" className="text-lg font-semibold text-slate-950">3. 准备 5–20 位首批客户</h2><p className="mt-1 text-sm text-slate-600">预览和验证阶段不会写入正式客户库，也不会发送邮件。</p></div><ShieldCheck className="h-6 w-6 text-indigo-700" /></div>
        <div role="tablist" aria-label="客户来源" className="mt-5 flex w-fit rounded-lg bg-slate-100 p-1">
          <button type="button" role="tab" aria-selected={sourceMode === 'csv'} onClick={() => setSourceMode('csv')} className={cn('min-h-11 rounded-md px-4 text-sm font-semibold', sourceMode === 'csv' ? 'bg-white text-slate-950 shadow-sm' : 'text-slate-600')}><FileUp className="mr-2 inline h-4 w-4" />CSV 导入</button>
          <button type="button" role="tab" aria-selected={sourceMode === 'ai'} onClick={() => setSourceMode('ai')} className={cn('min-h-11 rounded-md px-4 text-sm font-semibold', sourceMode === 'ai' ? 'bg-white text-slate-950 shadow-sm' : 'text-slate-600')}><Search className="mr-2 inline h-4 w-4" />AI 找客户</button>
        </div>

        {sourceMode === 'csv' ? (
          <div role="tabpanel" className="mt-5 grid gap-4 sm:grid-cols-[1fr_auto] sm:items-end">
            <div><Label htmlFor="activation-csv">CSV 文件（最大 2 MB）</Label><input id="activation-csv" type="file" accept=".csv,text/csv" className={cn('mt-2 block min-h-11 w-full rounded-lg border px-3 py-2 text-sm file:mr-3 file:border-0 file:bg-transparent file:font-semibold', inputClass)} onChange={event => setFile(event.target.files?.[0])} /></div>
            <Button type="button" className="min-h-11" disabled={!file || Boolean(busy)} onClick={importCsv}>{busy === 'import' ? <LoaderCircle className="h-4 w-4 animate-spin motion-reduce:animate-none" /> : <FileUp className="h-4 w-4" />}安全预览</Button>
          </div>
        ) : (
          <div role="tabpanel" className="mt-5 grid gap-4 md:grid-cols-2">
            <div><Label htmlFor="search-name">批次名称</Label><Input id="search-name" className={cn('mt-2', inputClass)} value={searchDraft.name} onChange={event => setSearchDraft(value => ({ ...value, name: event.target.value }))} /></div>
            <div><Label htmlFor="search-summary">产品与客户价值</Label><Input id="search-summary" className={cn('mt-2', inputClass)} value={searchDraft.productSummary} onChange={event => setSearchDraft(value => ({ ...value, productSummary: event.target.value }))} placeholder="例：帮助家纺零售商缩短新品打样周期" /></div>
            <div><Label htmlFor="search-industries">目标行业</Label><Input id="search-industries" className={cn('mt-2', inputClass)} value={searchDraft.industries} onChange={event => setSearchDraft(value => ({ ...value, industries: event.target.value }))} placeholder="家纺零售，进口商" /></div>
            <div><Label htmlFor="search-roles">目标角色</Label><Input id="search-roles" className={cn('mt-2', inputClass)} value={searchDraft.roles} onChange={event => setSearchDraft(value => ({ ...value, roles: event.target.value }))} placeholder="采购负责人，品类经理" /></div>
            <div><Label htmlFor="search-regions">目标地区</Label><Input id="search-regions" className={cn('mt-2', inputClass)} value={searchDraft.regions} onChange={event => setSearchDraft(value => ({ ...value, regions: event.target.value }))} placeholder="英国，北欧" /></div>
            <div><Label htmlFor="search-limit">候选数（5–20）</Label><Input id="search-limit" type="number" min={5} max={20} className={cn('mt-2', inputClass)} value={searchDraft.limit} onChange={event => setSearchDraft(value => ({ ...value, limit: Number(event.target.value) }))} /></div>
            <label className="flex min-h-11 items-start gap-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950 md:col-span-2"><input type="checkbox" className="mt-1 h-4 w-4" checked={paidConfirmed} onChange={event => setPaidConfirmed(event.target.checked)} /><span><strong>我确认本次付费动作。</strong><br /><span className="text-xs">只有管理员已开启真实获客 Provider 时才会产生费用；未开启时后端会拒绝。</span></span></label>
            <Button type="button" className="min-h-11 md:col-span-2 md:w-fit" disabled={!paidConfirmed || searchDraft.productSummary.length < 3 || Boolean(busy)} onClick={searchCustomers}>{busy === 'search' ? <LoaderCircle className="h-4 w-4 animate-spin motion-reduce:animate-none" /> : <Search className="h-4 w-4" />}查找候选客户</Button>
          </div>
        )}

        {run ? (
          <div className="mt-7 border-t border-slate-200 pt-5">
            <div className="flex flex-wrap items-center justify-between gap-3"><div><h3 className="font-semibold text-slate-950">{run.name}</h3><p className="mt-1 text-xs text-slate-500">状态：{run.status} · {candidates.length} 条候选 · 来源：{run.provider ?? run.source}</p>{Object.keys(run.column_mapping ?? {}).length ? <p className="mt-2 text-xs text-slate-600">已识别字段：{Object.entries(run.column_mapping ?? {}).map(([field, column]) => `${String(column)} → ${field}`).join('；')}</p> : null}</div><Button type="button" variant="outline" className="min-h-11" onClick={() => void refresh()} disabled={Boolean(busy)}>刷新状态</Button></div>
            <div className="mt-4 overflow-x-auto rounded-lg border border-slate-200">
              <table className="w-full min-w-[760px] text-left text-sm"><thead className="bg-slate-50 text-xs text-slate-600"><tr><th className="px-3 py-3"><span className="sr-only">选择</span></th><th className="px-3 py-3">公司</th><th className="px-3 py-3">联系人</th><th className="px-3 py-3">邮箱</th><th className="px-3 py-3">状态</th><th className="px-3 py-3">证据</th></tr></thead><tbody className="divide-y divide-slate-200">
                {shownCandidates.map(candidate => {
                  const canSelect = candidate.status === 'ready' || candidate.status === 'selected';
                  const evidenceUrl = safeHttpUrl(candidate.source_url);
                  return <tr key={candidate.id} className="bg-white align-top"><td className="px-3 py-3"><input type="checkbox" aria-label={`选择 ${candidate.company_name ?? candidateLabel(candidate)}`} disabled={!canSelect || (selectedIds.length >= 20 && !selectedIds.includes(candidate.id))} checked={selectedIds.includes(candidate.id)} onChange={event => setSelectedIds(ids => event.target.checked ? [...ids, candidate.id].slice(0, 20) : ids.filter(id => id !== candidate.id))} /></td><td className="px-3 py-3 font-medium text-slate-900">{candidate.company_name ?? '待确认'}<p className="mt-1 text-xs font-normal text-slate-500">{candidate.normalized_domain ?? '无可用域名'}</p></td><td className="px-3 py-3 text-slate-700">{candidateLabel(candidate)}<p className="mt-1 text-xs text-slate-500">{candidate.job_title ?? '职位待确认'}</p></td><td className="px-3 py-3 text-slate-700">{candidate.email ?? '验证后才显示'}</td><td className="px-3 py-3"><span className={cn('rounded-full px-2 py-1 text-xs font-semibold', candidate.status === 'invalid' ? 'bg-rose-100 text-rose-800' : candidate.status === 'committed' || candidate.verification_status === 'valid' ? 'bg-emerald-100 text-emerald-800' : candidate.status === 'duplicate' ? 'bg-amber-100 text-amber-900' : 'bg-slate-100 text-slate-700')}>{statusLabel(candidate)}</span>{candidate.rejection_reason ? <p className="mt-2 max-w-56 text-xs text-rose-700">{candidate.rejection_reason}</p> : null}</td><td className="max-w-64 px-3 py-3"><p className="text-xs leading-5 text-slate-600">{String(candidate.evidence.snippet ?? candidate.evidence.source ?? '暂无来源摘要')}</p>{evidenceUrl ? <a href={evidenceUrl} target="_blank" rel="noreferrer" className="mt-1 inline-flex text-xs font-semibold text-indigo-700 hover:underline">打开来源</a> : null}</td></tr>;
                })}
              </tbody></table>
            </div>
            {candidates.length > shownCandidates.length ? <p className="mt-2 text-xs text-slate-500">当前仅展示前 {shownCandidates.length} 条；首批试跑最多选择 20 人。</p> : null}
            <div className="mt-4 grid gap-3 lg:grid-cols-2">
              <div className="rounded-lg border border-slate-200 bg-slate-50 p-4"><h4 className="text-sm font-semibold text-slate-900">验证所选邮箱</h4><p className="mt-1 text-xs leading-5 text-slate-600">会产生 Provider 费用；只有明确 valid 才可入库。</p><label className="mt-2 flex items-start gap-2 text-xs leading-5 text-slate-700"><input type="checkbox" className="mt-1 h-4 w-4" checked={paidConfirmed} onChange={event => setPaidConfirmed(event.target.checked)} /><span>我确认验证所选邮箱可能产生 Provider 费用。</span></label><Button type="button" variant="outline" className="mt-3 min-h-11" disabled={!selectedIds.length || !paidConfirmed || Boolean(busy)} onClick={verifySelected}>{busy === 'verify' ? <LoaderCircle className="h-4 w-4 animate-spin motion-reduce:animate-none" /> : <ShieldCheck className="h-4 w-4" />}验证 {selectedIds.length} 人</Button></div>
              <div className="rounded-lg border border-slate-200 bg-slate-50 p-4"><h4 className="text-sm font-semibold text-slate-900">人工确认后入库</h4><label className="mt-2 flex items-start gap-2 text-xs leading-5 text-slate-700"><input type="checkbox" className="mt-1 h-4 w-4" checked={commitConfirmed} onChange={event => setCommitConfirmed(event.target.checked)} /><span>我已核对公司、联系人、邮箱和证据，确认写入正式客户库。</span></label><Button type="button" className="mt-3 min-h-11" disabled={!verifiedIds.length || !commitConfirmed || Boolean(busy)} onClick={commitVerified}>{busy === 'commit' ? <LoaderCircle className="h-4 w-4 animate-spin motion-reduce:animate-none" /> : <Check className="h-4 w-4" />}确认入库 {verifiedIds.length} 人</Button></div>
            </div>
          </div>
        ) : null}
      </section>

      {!acquisitionOnly ? (
        <section aria-labelledby="plan-heading" className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
          <h2 id="plan-heading" className="text-lg font-semibold text-slate-950">4. 创建逐封审核计划</h2><p className="mt-1 text-sm text-slate-600">这一步只创建不可变版本和审核任务；未批准草稿不会发送。</p>
          {!selectedAccount ? <div role="alert" className="mt-4 flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-950"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /><span>没有健康且凭据完整的 Email 账户。<Link href="/dashboard/settings/channels" className="ml-1 font-semibold underline">前往渠道设置</Link></span></div> : <p className="mt-4 rounded-lg bg-slate-50 px-4 py-3 text-sm text-slate-700">发件账户：<strong>{selectedAccount.address}</strong> · 每日上限 {selectedAccount.dailyLimit ?? '未设置'}</p>}
          <div className="mt-5 grid gap-4 md:grid-cols-2"><div><Label htmlFor="plan-name">计划名称</Label><Input id="plan-name" className={cn('mt-2', inputClass)} value={launchDraft.planName} onChange={event => { setLaunchDraft(value => ({ ...value, planName: event.target.value })); setLaunchPreview(undefined); }} /></div><div><Label htmlFor="plan-objective">本次目标</Label><Input id="plan-objective" className={cn('mt-2', inputClass)} value={launchDraft.objective} onChange={event => { setLaunchDraft(value => ({ ...value, objective: event.target.value })); setLaunchPreview(undefined); }} /></div><div><Label htmlFor="plan-subject">邮件主题</Label><Input id="plan-subject" className={cn('mt-2', inputClass)} value={launchDraft.subject} onChange={event => { setLaunchDraft(value => ({ ...value, subject: event.target.value })); setLaunchPreview(undefined); }} /></div><div><Label htmlFor="plan-limit">每日上限</Label><Input id="plan-limit" type="number" min={1} max={20} className={cn('mt-2', inputClass)} value={launchDraft.dailyLimit} onChange={event => { setLaunchDraft(value => ({ ...value, dailyLimit: Number(event.target.value) })); setLaunchPreview(undefined); }} /></div><div className="md:col-span-2"><Label htmlFor="plan-body">邮件正文</Label><Textarea id="plan-body" rows={7} className="mt-2 border-slate-300 bg-white text-slate-950" value={launchDraft.body} onChange={event => { setLaunchDraft(value => ({ ...value, body: event.target.value })); setLaunchPreview(undefined); }} /><p className="mt-1 text-xs text-slate-500">支持 company_name、company_domain、contact_name、first_name、job_title、unsubscribe_url。</p></div></div>
          <Button type="button" variant="outline" className="mt-5 min-h-11" disabled={!launchPayload || Boolean(busy)} onClick={previewLaunch}>{busy === 'preview-launch' ? <LoaderCircle className="h-4 w-4 animate-spin motion-reduce:animate-none" /> : <ShieldCheck className="h-4 w-4" />}生成启动预检</Button>
          {launchPreview ? <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-4"><h3 className="text-sm font-semibold text-slate-900">启动影响</h3><ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-slate-700">{launchPreview.effects.map(effect => <li key={effect}>{effect}</li>)}</ul>{launchPreview.blockers.length ? <div role="alert" className="mt-3 rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-900"><strong>必须先处理：</strong>{launchPreview.blockers.join('；')}</div> : <label className="mt-4 flex items-start gap-2 text-sm text-slate-800"><input type="checkbox" className="mt-1 h-4 w-4" checked={launchConfirmed} onChange={event => setLaunchConfirmed(event.target.checked)} /><span>我确认创建 {launchPreview.candidate_count} 人试跑，并对每封草稿人工审核。</span></label>}<Button type="button" className="mt-4 min-h-11" disabled={launchPreview.blockers.length > 0 || !launchConfirmed || Boolean(busy)} onClick={launch}>{busy === 'launch' ? <LoaderCircle className="h-4 w-4 animate-spin motion-reduce:animate-none" /> : <Send className="h-4 w-4" />}创建试跑计划</Button></div> : null}
        </section>
      ) : null}

      {!acquisitionOnly ? (
        <section aria-live="polite" className={cn('rounded-xl border p-5', activation?.activated ? 'border-emerald-300 bg-emerald-50' : 'border-indigo-200 bg-indigo-50')}>
          <div className="flex items-start gap-3">
            {activation?.activated ? <CheckCircle2 className="mt-0.5 h-5 w-5 text-emerald-700" /> : <LoaderCircle className="mt-0.5 h-5 w-5 text-indigo-700 motion-safe:animate-spin" />}
            <div>
              <h2 className="font-semibold text-slate-950">{activation?.activated ? '第一封邮件已成功发送' : activation?.campaign_id ? '试跑正在等待逐封审核' : '5. 在审核中批准第一封邮件'}</h2>
              <p className="mt-1 text-sm leading-6 text-slate-600">{activation?.activated ? `完成时间：${activation.first_sent_at ? new Date(activation.first_sent_at).toLocaleString('zh-CN') : '刚刚'}` : activation?.campaign_id ? `当前还有 ${activation.review_tasks_open} 封草稿等待审核；页面会持续检查首封发送状态。` : launchJobId ? `启动任务 #${launchJobId} 已接收，正在创建审核草稿。` : '计划创建后，草稿会进入“今日工作”的发送审批队列。'}</p>
              {!activation?.activated ? <Link href="/dashboard/work" className="mt-3 inline-flex min-h-11 items-center text-sm font-semibold text-indigo-800">前往今日工作审核邮件<ArrowRight className="ml-2 h-4 w-4" /></Link> : null}
            </div>
          </div>
        </section>
      ) : null}
    </ProductPageShell>
  );
}
