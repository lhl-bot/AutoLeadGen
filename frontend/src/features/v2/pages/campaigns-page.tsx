'use client';

import { useEffect, useState, type FormEvent } from 'react';
import Link from 'next/link';
import { CheckCircle2, CircleStop, FileDiff, GitBranch, LockKeyhole, Pause, Play, Plus, Send, ShieldCheck } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { v2Api, type CampaignChannel, type CampaignCommandAction } from '../api';
import type { Campaign, CampaignAuthoringSnapshot, DataEnvelope, RevisionImpactPreview } from '../types';
import { useV2Query } from '../use-v2-query';
import { useAdvancedMode } from '../use-advanced-mode';
import { EmptyState, LoadingState, ProductPageShell, QueryErrorState, ReadinessList, SourceBanner, StatusPill } from '../components/product-ui';

const actionCopy: Record<CampaignCommandAction, { label: string; effects: string[] }> = {
  start: {
    label: '启动',
    effects: ['创建异步启动任务，由 worker 再次检查全部硬门槛', '符合条件的 Enrollment 才会进入执行；本地仍受 fake connector 限制'],
  },
  pause: {
    label: '暂停',
    effects: ['创建异步暂停任务，停止新的触达 claim', '已写入的消息、成本与审计事件不会删除'],
  },
  complete: {
    label: '完成',
    effects: ['创建异步完成任务，结束 Campaign 生命周期', '历史 Enrollment、Conversation 与分析数据继续保留'],
  },
};

export function CampaignActionControls({
  campaign,
  onComplete,
  writeEnabled = true,
}: {
  campaign: Campaign;
  onComplete: () => void;
  writeEnabled?: boolean;
}) {
  const [preview, setPreview] = useState<CampaignCommandAction | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const blockers = campaign.readiness.filter(check => check.severity === 'blocker' && !check.passed);
  const warnings = campaign.readiness.filter(check => check.severity === 'warning' && !check.passed);
  const startDisabled = !writeEnabled || blockers.length > 0 || !['ready', 'paused'].includes(campaign.lifecycle);
  const pauseDisabled = !writeEnabled || campaign.lifecycle !== 'running';
  const completeDisabled = !writeEnabled || !['running', 'paused'].includes(campaign.lifecycle);

  const confirm = async () => {
    if (!preview || !writeEnabled) return;
    setPending(true);
    setError(null);
    try {
      const job = await v2Api.campaignCommand(campaign.id, preview, preview === 'start' && warnings.length > 0);
      toast.success(`${actionCopy[preview].label}任务已创建（Job #${job.job_id}）`);
      setPreview(null);
      onComplete();
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : 'Campaign 命令提交失败';
      setError(message);
      toast.error(message);
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="mt-5 border-t border-slate-100 pt-4">
      <div className="flex flex-wrap gap-2" aria-label={`${campaign.name} 生命周期操作`}>
        <Button type="button" className="min-h-11" disabled={startDisabled || pending} onClick={() => setPreview('start')} title={blockers.length ? '请先解决 readiness blocker' : undefined}>
          <Play className="h-4 w-4" />启动
        </Button>
        <Button type="button" variant="outline" className="min-h-11" disabled={pauseDisabled || pending} onClick={() => setPreview('pause')}>
          <Pause className="h-4 w-4" />暂停
        </Button>
        <Button type="button" variant="outline" className="min-h-11" disabled={completeDisabled || pending} onClick={() => setPreview('complete')}>
          <CircleStop className="h-4 w-4" />完成
        </Button>
        {blockers.length ? <span className="self-center text-xs font-medium text-rose-700">有 blocker，启动已禁用</span> : null}
      </div>
      {preview ? (
        <section aria-label={`${actionCopy[preview].label}影响预览`} className="mt-3 rounded-lg border border-indigo-200 bg-indigo-50 p-4">
          <h3 className="text-sm font-semibold text-indigo-950">确认{actionCopy[preview].label}「{campaign.name}」</h3>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-xs leading-5 text-indigo-900">
            {actionCopy[preview].effects.map(effect => <li key={effect}>{effect}</li>)}
            {preview === 'start' && warnings.length ? <li>{warnings.length} 个 warning 将被明确确认并写入审计</li> : null}
          </ul>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button type="button" className="min-h-11" disabled={!writeEnabled || pending} onClick={confirm}>
              <CheckCircle2 className="h-4 w-4" />{pending ? '提交中…' : '确认提交'}
            </Button>
            <Button type="button" variant="ghost" className="min-h-11" disabled={pending} onClick={() => setPreview(null)}>取消</Button>
          </div>
        </section>
      ) : null}
      {error ? <p role="alert" className="mt-2 text-xs text-rose-700">{error}</p> : null}
    </div>
  );
}

const selectClassName = 'min-h-11 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-950 shadow-xs outline-none focus-visible:border-indigo-400 focus-visible:ring-3 focus-visible:ring-indigo-500/15 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:opacity-60';

function diffEntries(diff: Record<string, unknown>, key: 'added' | 'changed' | 'removed'): unknown[] {
  const value = diff[key];
  return Array.isArray(value) ? value : [];
}

function ImpactPreview({
  impact,
  reviewed,
  disabled,
  onReviewedChange,
  onPublish,
}: {
  impact: RevisionImpactPreview;
  reviewed: boolean;
  disabled: boolean;
  onReviewedChange: (value: boolean) => void;
  onPublish: () => void;
}) {
  const groups = [
    ['added', '新增'],
    ['changed', '变更'],
    ['removed', '删除'],
  ] as const;
  return (
    <section aria-labelledby="revision-impact-title" className="rounded-lg border border-indigo-200 bg-indigo-50 p-4">
      <div className="flex items-start gap-3">
        <FileDiff className="mt-0.5 h-5 w-5 shrink-0 text-indigo-700" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <h3 id="revision-impact-title" className="text-sm font-semibold text-indigo-950">发布影响预览</h3>
          <p className="mt-1 text-xs leading-5 text-indigo-900">
            DRAFT #{impact.proposedRevisionId} 对 {impact.baseRevisionId ? `published #${impact.baseRevisionId}` : '空基线'}。以下内容来自 V2 /diff，只有人工确认后才可发布。
          </p>
          <p className="mt-1 break-all font-mono text-[11px] text-indigo-800">审阅校验码 {impact.diffChecksum}</p>
          <div className="mt-3 grid gap-3 md:grid-cols-3">
            {groups.map(([key, label]) => {
              const entries = diffEntries(impact.diff, key);
              return (
                <div key={key} className="rounded-md border border-indigo-100 bg-white/80 p-3">
                  <p className="text-xs font-semibold text-indigo-950">{label} {entries.length} 项</p>
                  {entries.length ? (
                    <ul className="mt-2 space-y-1 text-[11px] leading-5 text-slate-700">
                      {entries.slice(0, 6).map((entry, index) => <li key={`${key}-${index}`} className="break-words"><code>{JSON.stringify(entry)}</code></li>)}
                    </ul>
                  ) : <p className="mt-2 text-[11px] text-slate-500">无</p>}
                </div>
              );
            })}
          </div>
          <label className="mt-4 flex min-h-11 cursor-pointer items-center gap-3 rounded-md border border-indigo-200 bg-white px-3 py-2 text-sm font-medium text-indigo-950">
            <input type="checkbox" checked={reviewed} disabled={disabled} onChange={event => onReviewedChange(event.target.checked)} className="h-4 w-4" />
            我已审阅此 diff 及其受众、序列、预算和停止条件影响
          </label>
          <Button type="button" className="mt-3 min-h-11" disabled={disabled || !reviewed} onClick={onPublish}>
            <CheckCircle2 className="h-4 w-4" />人工确认并发布 DRAFT
          </Button>
        </div>
      </div>
    </section>
  );
}

export function CampaignAuthoringPanel({
  envelope,
  onRefresh,
}: {
  envelope: DataEnvelope<CampaignAuthoringSnapshot>;
  onRefresh: () => void;
}) {
  const canWrite = envelope.source === 'live';
  const [selectedCampaignId, setSelectedCampaignId] = useState('');
  const [selectedContactId, setSelectedContactId] = useState('');
  const [impact, setImpact] = useState<RevisionImpactPreview | null>(null);
  const [reviewed, setReviewed] = useState(false);
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!envelope.data.campaigns.some(campaign => campaign.id === selectedCampaignId)) {
      setSelectedCampaignId(envelope.data.campaigns[0]?.id ?? '');
      setImpact(null);
      setReviewed(false);
    }
  }, [envelope.data.campaigns, selectedCampaignId]);

  const selectedCampaign = envelope.data.campaigns.find(campaign => campaign.id === selectedCampaignId);
  const revisions = envelope.data.revisionsByCampaign[selectedCampaignId] ?? [];
  const draftRevisions = revisions
    .filter(revision => revision.status === 'draft')
    .sort((a, b) => b.revisionNumber - a.revisionNumber);
  const hasPublishedRevision = revisions.some(revision => revision.status === 'published');

  const fail = (reason: unknown, fallback: string) => {
    const message = reason instanceof Error ? reason.message : fallback;
    setError(message);
    toast.error(message);
  };

  const submitCampaign = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!canWrite) return;
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setPending('campaign');
    setError(null);
    try {
      const created = await v2Api.createCampaign({
        name: String(form.get('campaign_name') ?? '').trim(),
        description: String(form.get('campaign_description') ?? '').trim() || null,
        run_mode: String(form.get('campaign_mode') ?? 'shadow') as 'shadow' | 'review' | 'auto',
        priority: Number(form.get('campaign_priority') ?? 100),
      });
      setSelectedCampaignId(String(created.id));
      formElement.reset();
      toast.success(`Campaign 「${created.name}」已以 draft 创建`);
      onRefresh();
    } catch (reason) {
      fail(reason, 'Campaign 创建失败');
    } finally {
      setPending(null);
    }
  };

  const submitRevision = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!canWrite || !selectedCampaignId) return;
    const form = new FormData(event.currentTarget);
    const industries = String(form.get('icp_industries') ?? '').split(/[,，]/).map(value => value.trim()).filter(Boolean);
    setPending('revision');
    setError(null);
    try {
      const created = await v2Api.createDraftRevision(selectedCampaignId, {
        icp_definition: {
          summary: String(form.get('icp_summary') ?? '').trim(),
          industries,
        },
        audience_definition: { description: String(form.get('audience_description') ?? '').trim() },
        quality_gates: {
          min_fit_score: Number(form.get('min_fit_score') ?? 70),
          require_verified_contact_point: true,
        },
        budget_definition: {
          native_limit: Number(form.get('native_limit') ?? 0),
          native_unit: String(form.get('native_unit') ?? '').trim(),
        },
        stop_conditions: {
          public_unsubscribe_url: String(form.get('public_unsubscribe_url') ?? '').trim(),
          pause_contact_on_positive_signal: true,
          pause_company_on_qualified_opportunity: true,
        },
        sequence_steps: [{
          position: 1,
          channel: String(form.get('sequence_channel') ?? 'email') as CampaignChannel,
          wait_minutes: Number(form.get('wait_minutes') ?? 0),
          template_version: String(form.get('template_version') ?? '').trim(),
          subject_template: String(form.get('subject_template') ?? '').trim(),
          body_template: String(form.get('body_template') ?? '').trim(),
          conditions: {},
          stop_conditions: { stop_on_reply: true },
        }],
      });
      toast.success(`Revision #${created.revision_number} 已保存为 DRAFT，未发布`);
      setReviewed(false);
      try {
        setImpact(await v2Api.revisionDiff(selectedCampaignId, String(created.id)));
      } catch (reason) {
        fail(reason, 'DRAFT 已创建，但 diff 读取失败');
      }
      onRefresh();
    } catch (reason) {
      fail(reason, 'Revision DRAFT 创建失败');
    } finally {
      setPending(null);
    }
  };

  const previewRevision = async (revisionId: string) => {
    if (!selectedCampaignId) return;
    setPending(`diff:${revisionId}`);
    setError(null);
    try {
      setImpact(await v2Api.revisionDiff(selectedCampaignId, revisionId));
      setReviewed(false);
    } catch (reason) {
      fail(reason, 'Revision diff 读取失败');
    } finally {
      setPending(null);
    }
  };

  const publish = async () => {
    if (!canWrite || !impact || !reviewed || impact.campaignId !== selectedCampaignId) return;
    setPending('publish');
    setError(null);
    try {
      const published = await v2Api.publishRevision(impact);
      toast.success(`Revision #${published.revision_number} 已人工确认发布`);
      setImpact(null);
      setReviewed(false);
      onRefresh();
    } catch (reason) {
      fail(reason, 'Revision 发布失败');
    } finally {
      setPending(null);
    }
  };

  const submitEnrollment = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!canWrite || !selectedCampaignId || !selectedContactId) return;
    setPending('enrollment');
    setError(null);
    try {
      const job = await v2Api.enrollContact(selectedCampaignId, { contact_id: Number(selectedContactId), scheduled_at: null });
      toast.success(`Enrollment 任务已创建（Job #${job.job_id}）`);
      setSelectedContactId('');
      onRefresh();
    } catch (reason) {
      fail(reason, 'Enrollment 创建失败');
    } finally {
      setPending(null);
    }
  };

  return (
    <section aria-labelledby="campaign-authoring-title" className="space-y-5 rounded-xl border border-slate-200 bg-slate-50 p-4 sm:p-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 id="campaign-authoring-title" className="text-lg font-semibold text-slate-950">Campaign 编排台</h2>
          <p className="mt-1 text-sm leading-6 text-slate-600">创建 draft Campaign，再以不可变 DRAFT 提案、diff 审阅和人工发布完成版本化。</p>
        </div>
        {!canWrite ? (
          <div role="alert" className="flex items-center gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs font-semibold text-amber-950">
            <LockKeyhole className="h-4 w-4" />{envelope.source === 'mixed' ? '混合数据' : '示例数据'}：写操作锁定
          </div>
        ) : null}
      </div>

      <div className="grid gap-5 xl:grid-cols-2">
        <form onSubmit={submitCampaign} className="rounded-lg border border-slate-200 bg-white p-4">
          <fieldset disabled={!canWrite || pending !== null}>
            <legend className="text-sm font-semibold text-slate-950">1. 创建 Campaign draft</legend>
            <div className="mt-4 space-y-4">
              <div><Label htmlFor="campaign-name">名称</Label><Input id="campaign-name" name="campaign_name" className="mt-2 min-h-11" required maxLength={255} /></div>
              <div><Label htmlFor="campaign-description">说明</Label><Textarea id="campaign-description" name="campaign_description" className="mt-2" /></div>
              <div className="grid gap-4 sm:grid-cols-2">
                <div><Label htmlFor="campaign-mode">运行模式</Label><select id="campaign-mode" name="campaign_mode" defaultValue="shadow" className={`${selectClassName} mt-2`}><option value="shadow">shadow</option><option value="review">review</option><option value="auto">auto（本地仍只能 fake）</option></select></div>
                <div><Label htmlFor="campaign-priority">优先级</Label><Input id="campaign-priority" name="campaign_priority" type="number" min={0} max={1000} defaultValue={100} className="mt-2 min-h-11" required /></div>
              </div>
              <Button type="submit" className="min-h-11" disabled={!canWrite || pending !== null}><Plus className="h-4 w-4" />{pending === 'campaign' ? '创建中…' : '创建 draft Campaign'}</Button>
            </div>
          </fieldset>
        </form>

        <div className="rounded-lg border border-slate-200 bg-white p-4">
          <Label htmlFor="authoring-campaign">当前编排 Campaign</Label>
          <select id="authoring-campaign" className={`${selectClassName} mt-2`} value={selectedCampaignId} disabled={!canWrite || !envelope.data.campaigns.length || pending !== null} onChange={event => { setSelectedCampaignId(event.target.value); setImpact(null); setReviewed(false); }}>
            {!envelope.data.campaigns.length ? <option value="">尚无 Campaign</option> : null}
            {envelope.data.campaigns.map(campaign => <option key={campaign.id} value={campaign.id}>{campaign.name} · {campaign.lifecycle}</option>)}
          </select>
          <p className="mt-3 text-xs leading-5 text-slate-600">
            {selectedCampaign ? `当前状态 ${selectedCampaign.lifecycle}，${revisions.length} 个 Revision。` : '先创建 Campaign 再编排 Revision。'} Readiness blocker 仍由后端作为最终启动门槛。
          </p>
          {draftRevisions.length ? (
            <div className="mt-4">
              <p className="text-xs font-semibold text-slate-700">未发布 DRAFT</p>
              <div className="mt-2 flex flex-wrap gap-2">
                {draftRevisions.map(revision => (
                  <Button key={revision.id} type="button" variant="outline" className="min-h-11" disabled={!canWrite || pending !== null} onClick={() => previewRevision(revision.id)}>
                    <FileDiff className="h-4 w-4" />{pending === `diff:${revision.id}` ? '读取中…' : `预览 Revision #${revision.revisionNumber}`}
                  </Button>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      </div>

      <form onSubmit={submitRevision} className="rounded-lg border border-slate-200 bg-white p-4">
        <fieldset disabled={!canWrite || !selectedCampaignId || pending !== null}>
          <legend className="text-sm font-semibold text-slate-950">2. 创建不可变 Revision DRAFT（不直接发布）</legend>
          <div className="mt-4 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            <div className="md:col-span-2"><Label htmlFor="icp-summary">ICP 简述</Label><Textarea id="icp-summary" name="icp_summary" className="mt-2" required /></div>
            <div><Label htmlFor="icp-industries">行业（逗号分隔）</Label><Input id="icp-industries" name="icp_industries" className="mt-2 min-h-11" required /></div>
            <div className="md:col-span-2"><Label htmlFor="audience-description">受众说明</Label><Textarea id="audience-description" name="audience_description" className="mt-2" required /></div>
            <div><Label htmlFor="min-fit-score">最低 Fit 分</Label><Input id="min-fit-score" name="min_fit_score" type="number" min={0} max={100} defaultValue={70} className="mt-2 min-h-11" required /></div>
            <div><Label htmlFor="native-limit">Provider 预算 native_limit</Label><Input id="native-limit" name="native_limit" type="number" min={1} step="any" className="mt-2 min-h-11" required /></div>
            <div><Label htmlFor="native-unit">预算 native_unit</Label><Input id="native-unit" name="native_unit" defaultValue="fake_calls" className="mt-2 min-h-11" required /></div>
            <div><Label htmlFor="unsubscribe-url">公共退订 URL</Label><Input id="unsubscribe-url" name="public_unsubscribe_url" type="url" placeholder="https://app.example.com/api/unsubscribe/v2" className="mt-2 min-h-11" required /></div>
          </div>
          <fieldset className="mt-5 rounded-lg border border-slate-200 p-4">
            <legend className="px-1 text-xs font-semibold text-slate-700">Sequence Step 1（至少一步）</legend>
            <div className="grid gap-4 sm:grid-cols-3">
              <div><Label htmlFor="sequence-channel">渠道</Label><select id="sequence-channel" name="sequence_channel" defaultValue="email" className={`${selectClassName} mt-2`}><option value="email">Email（当前生产版本）</option></select></div>
              <div><Label htmlFor="wait-minutes">等待分钟</Label><Input id="wait-minutes" name="wait_minutes" type="number" min={0} defaultValue={0} className="mt-2 min-h-11" required /></div>
              <div><Label htmlFor="template-version">模板版本</Label><Input id="template-version" name="template_version" placeholder="cold-email-v1" maxLength={100} className="mt-2 min-h-11" required /></div>
            </div>
            <div className="mt-4 grid gap-4 md:grid-cols-2">
              <div><Label htmlFor="subject-template">主题模板（Email）</Label><Input id="subject-template" name="subject_template" placeholder="{{company_name}} 的采购合作" maxLength={1000} className="mt-2 min-h-11" /></div>
              <div className="md:col-span-2"><Label htmlFor="body-template">正文模板</Label><Textarea id="body-template" name="body_template" placeholder={'Hi {{first_name}},\n\n...\n\n退订：{{unsubscribe_url}}'} maxLength={20000} className="mt-2 min-h-40" required /></div>
              <p className="md:col-span-2 text-xs text-slate-600">当前生产版本只开放 Email；LinkedIn 与 WhatsApp 连接器通过独立生产验收后才会开放。可用变量：company_name、company_domain、contact_name、first_name、job_title、unsubscribe_url。发布后模板随 Revision 不可变。</p>
            </div>
          </fieldset>
          <Button type="submit" className="mt-5 min-h-11" disabled={!canWrite || !selectedCampaignId || pending !== null}><FileDiff className="h-4 w-4" />{pending === 'revision' ? '保存中…' : '保存 DRAFT 并读取 diff'}</Button>
        </fieldset>
      </form>

      {impact ? <ImpactPreview impact={impact} reviewed={reviewed} disabled={!canWrite || pending !== null} onReviewedChange={setReviewed} onPublish={publish} /> : null}

      <form onSubmit={submitEnrollment} className="rounded-lg border border-slate-200 bg-white p-4">
        <fieldset disabled={!canWrite || !selectedCampaignId || !hasPublishedRevision || pending !== null}>
          <legend className="text-sm font-semibold text-slate-950">3. 从 V2 Contacts 创建 Enrollment</legend>
          <p className="mt-2 text-xs leading-5 text-slate-600">只展示 V2 Contact 的真实联系点状态，不伪造验证或账户健康；后端 Consent、联系点和多 Campaign 冲突规则保持最终裁决。</p>
          {!hasPublishedRevision ? <p className="mt-2 text-xs font-semibold text-amber-800">请先审阅 diff 并发布 Revision。</p> : null}
          <div className="mt-4 flex flex-col gap-3 md:flex-row md:items-end">
            <div className="min-w-0 flex-1">
              <Label htmlFor="enrollment-contact">Contact</Label>
              <select id="enrollment-contact" value={selectedContactId} onChange={event => setSelectedContactId(event.target.value)} className={`${selectClassName} mt-2`} required>
                <option value="">选择联系人</option>
                {envelope.data.contacts.map(contact => <option key={contact.id} value={contact.id}>{contact.label} · {contact.company} · {contact.contactPoints.join(' | ') || '无联系点'}</option>)}
              </select>
            </div>
            <Button type="submit" className="min-h-11" disabled={!canWrite || !selectedCampaignId || !selectedContactId || !hasPublishedRevision || pending !== null}><Send className="h-4 w-4" />{pending === 'enrollment' ? '入组中…' : '创建 Enrollment 任务'}</Button>
          </div>
        </fieldset>
      </form>
      {error ? <p role="alert" className="text-sm text-rose-700">{error}</p> : null}
    </section>
  );
}

export default function CampaignsPage() {
  const { result, loading, error, refresh } = useV2Query(v2Api.campaignAuthoring);
  const advanced = useAdvancedMode();
  const [editorOpen, setEditorOpen] = useState(false);
  return (
    <ProductPageShell eyebrow="触达计划" title="触达计划" description="查看正在准备、审核和发送的计划。首次试跑默认逐封确认，不会自动群发。">
      {error ? <QueryErrorState error={error} onRetry={refresh} /> : loading || !result ? <LoadingState label="正在读取 Campaign Revision 与 readiness…" /> : (
        <>
          <SourceBanner envelope={result} onRefresh={refresh} />
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-200 bg-white p-4"><p className="text-sm text-slate-600">新用户请从首次触达向导创建 5–20 人试跑。</p><div className="flex gap-2"><Link href="/dashboard/get-started?step=4" className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-medium text-white hover:bg-slate-800"><Plus className="h-4 w-4" />创建触达计划</Link>{advanced ? <Button type="button" variant="outline" onClick={() => setEditorOpen(true)}><FileDiff className="h-4 w-4" />高级编辑器</Button> : null}</div></div>
          <Dialog open={editorOpen} onOpenChange={setEditorOpen}>
            <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-6xl">
              <DialogHeader><DialogTitle>触达计划高级编辑器</DialogTitle><DialogDescription>Revision、diff、native budget、Enrollment 和运行模式只在高级模式展示。</DialogDescription></DialogHeader>
              <CampaignAuthoringPanel envelope={result} onRefresh={refresh} />
            </DialogContent>
          </Dialog>
          {result.data.campaigns.length ? <div className="space-y-5">{result.data.campaigns.map(campaign => {
            const blockers = campaign.readiness.filter(check => check.severity === 'blocker' && !check.passed);
            return (
              <article key={campaign.id} className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
                <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                  <div><div className="flex flex-wrap items-center gap-2"><h2 className="text-lg font-semibold text-slate-950">{campaign.name}</h2><span className="rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-xs font-semibold text-slate-700">{campaign.lifecycle}</span>{advanced ? <span className="rounded-full border border-indigo-200 bg-indigo-50 px-2.5 py-1 text-xs font-semibold text-indigo-800">{campaign.mode}</span> : null}</div><p className="mt-2 text-xs text-slate-500">{advanced ? `优先级 ${campaign.priority} · ${campaign.enrollments} 个 Enrollment · ${campaign.positiveSignals} 个正向信号 · native budget ${campaign.budgetLimit ?? '未发布'}` : `${campaign.enrollments} 名联系人 · ${campaign.positiveSignals} 个正向回复`}</p></div>
                  <div className={`rounded-lg border px-3 py-2 text-xs font-semibold ${blockers.length ? 'border-rose-200 bg-rose-50 text-rose-800' : 'border-emerald-200 bg-emerald-50 text-emerald-800'}`}><ShieldCheck className="mr-1 inline h-4 w-4" />{blockers.length ? `${blockers.length} 个 blocker，禁止启动` : '无 readiness blocker'}</div>
                </div>
                <section aria-label={`${campaign.name} 阶段状态`} className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
                  {campaign.stages.map((stage, index) => <div key={stage.key} className="relative rounded-lg border border-slate-200 bg-slate-50 p-3"><div className="flex items-center justify-between gap-2"><span className="text-sm font-semibold text-slate-900">{index + 1}. {stage.label}</span><StatusPill state={stage.state} /></div><p className="mt-2 text-xs leading-5 text-slate-500">{stage.detail}</p>{index < campaign.stages.length - 1 ? <GitBranch className="absolute -right-2 top-4 hidden h-4 w-4 text-slate-300 xl:block" aria-hidden="true" /> : null}</div>)}
                </section>
                {advanced ? <><div className="mt-5"><h3 className="mb-3 text-sm font-semibold text-slate-950">Readiness</h3><ReadinessList checks={campaign.readiness} /></div><CampaignActionControls campaign={campaign} onComplete={refresh} writeEnabled={result.source === 'live'} /></> : null}
              </article>
            );
          })}</div> : <EmptyState title="尚无 V2 Campaign" detail="先创建 Campaign，再发布包含 ICP、受众、序列、预算与停止条件的 Revision。" />}
        </>
      )}
    </ProductPageShell>
  );
}
