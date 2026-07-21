'use client';

import { FormEvent, useState } from 'react';
import {
  AlertCircle,
  ArrowRight,
  CalendarClock,
  ClipboardCheck,
  MessageSquareText,
  ShieldCheck,
  UserRoundCheck,
} from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { v2Api } from '../api';
import { useV2Query } from '../use-v2-query';
import type { Opportunity, OpportunityStage, SalesHandoff } from '../types';
import { EmptyState, LoadingState, ProductPageShell, QueryErrorState, SourceBanner, formatDate } from '../components/product-ui';

const stages: Array<[OpportunityStage, string]> = [
  ['qualified_reply', 'Qualified reply'], ['discovery', 'Discovery'], ['sample_or_quote', 'Sample / Quote'], ['negotiation', 'Negotiation'], ['won', 'Won'], ['lost', 'Lost'],
];

const stageLabels = Object.fromEntries(stages) as Record<OpportunityStage, string>;
const nextStage: Partial<Record<OpportunityStage, OpportunityStage>> = {
  qualified_reply: 'discovery',
  discovery: 'sample_or_quote',
  sample_or_quote: 'negotiation',
};

interface OpportunityConfirmationPayload {
  reply_assessment_id: number;
  source_task_id: number;
  assignee_user_id: number;
  next_action: string;
  next_action_due_at: string;
  fit_confirmed: boolean;
  value_amount?: number;
  currency?: string;
  expected_close_date?: string;
}

function OpportunityCard({ item, mutable, onUpdated }: { item: Opportunity; mutable: boolean; onUpdated: () => void }) {
  const naturalNext = nextStage[item.stage];
  const targets = naturalNext ? [naturalNext, 'won', 'lost'] as OpportunityStage[] : ['won', 'lost'] as OpportunityStage[];
  const terminal = item.stage === 'won' || item.stage === 'lost';
  const [target, setTarget] = useState<OpportunityStage>(targets[0] ?? item.stage);
  const [valueAmount, setValueAmount] = useState(item.value === undefined ? '' : String(item.value));
  const [currency, setCurrency] = useState(item.currency ?? 'USD');
  const [dealDate, setDealDate] = useState(new Date().toISOString().slice(0, 10));
  const [lostReason, setLostReason] = useState('');
  const [pending, setPending] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (terminal || !mutable) return;
    setPending(true);
    try {
      const payload: {
        stage: OpportunityStage;
        value_amount?: number;
        currency?: string;
        deal_date?: string;
        lost_reason?: string;
      } = { stage: target };
      if (target === 'won') {
        payload.value_amount = Number(valueAmount);
        payload.currency = currency.trim().toUpperCase();
        payload.deal_date = dealDate;
      }
      if (target === 'lost') payload.lost_reason = lostReason.trim();
      await v2Api.updateOpportunityStage(item.id, payload);
      toast.success(`商机已推进至 ${stageLabels[target]}`);
      onUpdated();
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : '商机阶段更新失败');
    } finally {
      setPending(false);
    }
  };

  return (
    <article className="rounded-lg border border-slate-200 bg-white p-3 shadow-sm">
      <h3 className="text-sm font-semibold text-slate-950">{item.company}</h3>
      <p className="mt-1 text-xs text-slate-500">{item.contact}</p>
      <p className="mt-3 text-xs leading-5 text-slate-700">下一步：{item.nextStep}</p>
      <p className="mt-2 flex items-center gap-1 text-[11px] text-slate-500"><CalendarClock className="h-3 w-3" />{formatDate(item.nextActionDueAt)}</p>
      <p className="mt-1 flex items-center gap-1 text-[11px] text-slate-500"><UserRoundCheck className="h-3 w-3" />负责人 #{item.ownerId}</p>
      {item.value !== undefined ? <p className="mt-2 text-xs font-semibold text-emerald-800">{item.currency} {item.value.toLocaleString()}</p> : null}
      {!terminal ? (
        <form onSubmit={submit} className="mt-3 space-y-2 border-t border-slate-100 pt-3">
          <label className="block text-[11px] font-semibold text-slate-700">
            推进至
            <select value={target} onChange={event => setTarget(event.target.value as OpportunityStage)} disabled={!mutable || pending} className="mt-1 min-h-11 w-full rounded-lg border border-slate-300 bg-white px-2 text-xs outline-none focus-visible:border-indigo-500 focus-visible:ring-2 focus-visible:ring-indigo-200 disabled:bg-slate-100">
              {targets.map(value => <option key={value} value={value}>{stageLabels[value]}</option>)}
            </select>
          </label>
          {target === 'won' ? (
            <div className="space-y-2 rounded-lg bg-emerald-50 p-2">
              <label className="block text-[11px] font-semibold text-emerald-950">金额<input aria-label="成交金额" type="number" min="0" step="0.01" required value={valueAmount} onChange={event => setValueAmount(event.target.value)} className="mt-1 min-h-11 w-full rounded-lg border border-emerald-200 bg-white px-2 text-xs" /></label>
              <label className="block text-[11px] font-semibold text-emerald-950">币种<input aria-label="成交币种" minLength={3} maxLength={3} required value={currency} onChange={event => setCurrency(event.target.value)} className="mt-1 min-h-11 w-full rounded-lg border border-emerald-200 bg-white px-2 text-xs uppercase" /></label>
              <label className="block text-[11px] font-semibold text-emerald-950">成交日期<input aria-label="成交日期" type="date" required value={dealDate} onChange={event => setDealDate(event.target.value)} className="mt-1 min-h-11 w-full rounded-lg border border-emerald-200 bg-white px-2 text-xs" /></label>
            </div>
          ) : null}
          {target === 'lost' ? <label className="block text-[11px] font-semibold text-rose-800">丢单原因<textarea aria-label="丢单原因" required value={lostReason} onChange={event => setLostReason(event.target.value)} className="mt-1 min-h-20 w-full rounded-lg border border-rose-200 bg-white p-2 text-xs" /></label> : null}
          <Button type="submit" className="min-h-11 w-full" disabled={!mutable || pending} title={!mutable ? '示例或混合数据不可写入' : undefined}>
            <ArrowRight className="h-4 w-4" />{pending ? '更新中…' : '确认推进'}
          </Button>
        </form>
      ) : <p className="mt-3 rounded-lg bg-slate-100 p-2 text-center text-xs font-semibold text-slate-600">终态商机</p>}
    </article>
  );
}

function HandoffCard({ item, mutable, onCreated }: { item: SalesHandoff; mutable: boolean; onCreated: () => void }) {
  const [assigneeUserId, setAssigneeUserId] = useState(item.assigneeUserId ?? '');
  const [nextAction, setNextAction] = useState('');
  const [nextActionDueAt, setNextActionDueAt] = useState('');
  const [fitConfirmed, setFitConfirmed] = useState(false);
  const [valueAmount, setValueAmount] = useState('');
  const [currency, setCurrency] = useState('USD');
  const [expectedCloseDate, setExpectedCloseDate] = useState('');
  const [preview, setPreview] = useState<OpportunityConfirmationPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const hasCompleteEvidence = Boolean(item.replyAssessmentId && item.companyId && item.contactId && item.conversationId);

  const invalidatePreview = () => {
    setPreview(null);
    setError(null);
  };

  const preparePreview = (event: FormEvent) => {
    event.preventDefault();
    if (!mutable) return;
    const assignee = Number(assigneeUserId);
    const sourceTaskId = Number(item.id);
    const assessmentId = Number(item.replyAssessmentId);
    const parsedDueAt = new Date(nextActionDueAt);
    if (!hasCompleteEvidence || !Number.isInteger(sourceTaskId) || !Number.isInteger(assessmentId)) {
      setError('交接任务缺少 reply assessment、公司、联系人或会话证据，无法确认。');
      return;
    }
    if (!Number.isInteger(assignee) || assignee <= 0) {
      setError('请填写有效的负责人用户 ID。');
      return;
    }
    if (!nextAction.trim()) {
      setError('请填写下一步动作。');
      return;
    }
    if (!nextActionDueAt || Number.isNaN(parsedDueAt.getTime())) {
      setError('请填写有效的下一步到期时间。');
      return;
    }
    if (!fitConfirmed) {
      setError('请确认该公司符合已发布 ICP。未确认时本页不会伪造 Fit override。');
      return;
    }
    const payload: OpportunityConfirmationPayload = {
      reply_assessment_id: assessmentId,
      source_task_id: sourceTaskId,
      assignee_user_id: assignee,
      next_action: nextAction.trim(),
      next_action_due_at: parsedDueAt.toISOString(),
      fit_confirmed: true,
    };
    if (valueAmount.trim()) {
      const value = Number(valueAmount);
      const normalizedCurrency = currency.trim().toUpperCase();
      if (!Number.isFinite(value) || value < 0) {
        setError('预估金额必须是大于或等于 0 的数字。');
        return;
      }
      if (!/^[A-Z]{3}$/.test(normalizedCurrency)) {
        setError('填写预估金额时，币种必须是 3 位代码，例如 USD。');
        return;
      }
      payload.value_amount = value;
      payload.currency = normalizedCurrency;
    }
    if (expectedCloseDate) payload.expected_close_date = expectedCloseDate;
    setError(null);
    setPreview(payload);
  };

  const confirm = async () => {
    if (!mutable || !preview) return;
    setPending(true);
    setError(null);
    try {
      await v2Api.confirmOpportunity(preview);
      toast.success(`已确认 ${item.company} 的合格商机`);
      setPreview(null);
      onCreated();
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : '合格商机创建失败';
      setError(message);
      toast.error(message);
    } finally {
      setPending(false);
    }
  };

  return (
    <article className="rounded-xl border border-indigo-200 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.12em] text-indigo-700">Task #{item.id} · {item.status}</p>
          <h3 className="mt-1 text-base font-semibold text-slate-950">{item.company}</h3>
          <p className="mt-1 text-sm text-slate-600">{item.contact}</p>
        </div>
        <span className="rounded-full bg-indigo-50 px-2.5 py-1 text-xs font-semibold text-indigo-800">{item.priority}</span>
      </div>
      <div className="mt-3 grid gap-2 text-xs text-slate-600 sm:grid-cols-2">
        <p className="flex items-center gap-1.5"><MessageSquareText className="h-4 w-4 text-indigo-600" />{item.conversation}</p>
        <p className="flex items-center gap-1.5"><ClipboardCheck className="h-4 w-4 text-indigo-600" />Reply assessment #{item.replyAssessmentId ?? '缺失'}</p>
      </div>
      <blockquote className="mt-3 rounded-lg border-l-4 border-indigo-200 bg-indigo-50/60 p-3 text-sm leading-6 text-slate-700">{item.detail}</blockquote>
      {!hasCompleteEvidence ? (
        <p role="alert" className="mt-3 flex items-start gap-2 rounded-lg bg-rose-50 p-3 text-sm text-rose-800">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />该 Task 缺少完整证据关联，已禁止创建商机，请先对账。
        </p>
      ) : null}
      <form onSubmit={preparePreview} className="mt-4 grid gap-3 border-t border-slate-200 pt-4 sm:grid-cols-2">
        <label className="text-xs font-semibold text-slate-700">
          负责人用户 ID
          <input aria-label="负责人用户 ID" type="number" min="1" step="1" required value={assigneeUserId} onChange={event => { setAssigneeUserId(event.target.value); invalidatePreview(); }} disabled={!mutable || pending} className="mt-1 min-h-11 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm disabled:bg-slate-100" />
        </label>
        <label className="text-xs font-semibold text-slate-700">
          下一步到期时间
          <input aria-label="下一步到期时间" type="datetime-local" required value={nextActionDueAt} onChange={event => { setNextActionDueAt(event.target.value); invalidatePreview(); }} disabled={!mutable || pending} className="mt-1 min-h-11 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm disabled:bg-slate-100" />
        </label>
        <label className="text-xs font-semibold text-slate-700 sm:col-span-2">
          下一步动作
          <textarea aria-label="下一步动作" required maxLength={1000} value={nextAction} onChange={event => { setNextAction(event.target.value); invalidatePreview(); }} disabled={!mutable || pending} className="mt-1 min-h-24 w-full rounded-lg border border-slate-300 bg-white p-3 text-sm disabled:bg-slate-100" />
        </label>
        <label className="text-xs font-semibold text-slate-700">
          预估金额（可选）
          <input aria-label="预估金额" type="number" min="0" step="0.01" value={valueAmount} onChange={event => { setValueAmount(event.target.value); invalidatePreview(); }} disabled={!mutable || pending} className="mt-1 min-h-11 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm disabled:bg-slate-100" />
        </label>
        <label className="text-xs font-semibold text-slate-700">
          币种
          <input aria-label="预估币种" minLength={3} maxLength={3} value={currency} onChange={event => { setCurrency(event.target.value); invalidatePreview(); }} disabled={!mutable || pending || !valueAmount.trim()} className="mt-1 min-h-11 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm uppercase disabled:bg-slate-100" />
        </label>
        <label className="text-xs font-semibold text-slate-700">
          预计成交日期（可选）
          <input aria-label="预计成交日期" type="date" value={expectedCloseDate} onChange={event => { setExpectedCloseDate(event.target.value); invalidatePreview(); }} disabled={!mutable || pending} className="mt-1 min-h-11 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm disabled:bg-slate-100" />
        </label>
        <label className="flex min-h-11 items-center gap-3 rounded-lg border border-amber-200 bg-amber-50 px-3 text-sm font-semibold text-amber-950">
          <input aria-label="确认符合已发布 ICP" type="checkbox" required checked={fitConfirmed} onChange={event => { setFitConfirmed(event.target.checked); invalidatePreview(); }} disabled={!mutable || pending} className="h-5 w-5 accent-indigo-700" />
          我已确认符合已发布 ICP
        </label>
        <p className="text-xs leading-5 text-slate-500 sm:col-span-2">如未确认 Fit，本页不会伪造或自动选择 Manual Override。</p>
        {error ? <p role="alert" className="rounded-lg bg-rose-50 p-3 text-sm text-rose-800 sm:col-span-2">{error}</p> : null}
        <Button type="submit" variant="outline" className="min-h-11 sm:col-span-2" disabled={!mutable || !hasCompleteEvidence || pending} title={!mutable ? '示例或混合数据不可写入' : undefined}>
          <ShieldCheck className="h-4 w-4" />预览确认影响
        </Button>
      </form>
      {preview ? (
        <section aria-labelledby={`handoff-preview-${item.id}`} className="mt-4 rounded-lg border border-amber-300 bg-amber-50 p-4">
          <h4 id={`handoff-preview-${item.id}`} className="text-sm font-semibold text-amber-950">影响确认</h4>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm leading-6 text-amber-950">
            <li>创建 Qualified reply 阶段的合格商机，负责人为用户 #{preview.assignee_user_id}。</li>
            <li>完成 sales_handoff Task #{item.id}，并保留消息、成本与审计历史。</li>
            <li>后端将暂停 {item.company} 的其他冷触达 Enrollment，保留当前会话和人工任务。</li>
          </ul>
          <div className="mt-3 flex flex-wrap justify-end gap-2">
            <Button type="button" variant="outline" className="min-h-11" disabled={pending} onClick={() => setPreview(null)}>返回修改</Button>
            <Button type="button" className="min-h-11" disabled={pending || !mutable} onClick={confirm}>
              {pending ? '创建中…' : '确认创建合格商机'}
            </Button>
          </div>
        </section>
      ) : null}
    </article>
  );
}

export default function OpportunitiesPage() {
  const { result, loading, error, refresh } = useV2Query(v2Api.opportunityWorkspace);
  return (
    <ProductPageShell eyebrow="North star" title="商机" description="AI 只能提议。销售确认 Fit、正向信号、负责人、下一步及到期时间后，才创建合格商机并暂停公司其他冷触达。">
      {error ? <QueryErrorState error={error} onRetry={refresh} /> : loading || !result ? <LoadingState label="正在读取 Opportunity 与 sales_handoff…" /> : (
        <>
          <SourceBanner envelope={result} onRefresh={refresh} />
          <section aria-labelledby="sales-handoff-heading" className="rounded-xl border border-indigo-200 bg-indigo-50/40 p-4 sm:p-5">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h2 id="sales-handoff-heading" className="text-base font-semibold text-slate-950">待确认销售交接</h2>
                <p className="mt-1 text-xs text-slate-600">仅显示 V2 中 open / in_progress 的 sales_handoff Task</p>
              </div>
              <span className="rounded-full bg-white px-2.5 py-1 text-xs font-semibold text-indigo-800">{result.data.handoffs.length}</span>
            </div>
            {result.data.handoffs.length ? (
              <div className="mt-4 grid gap-4 xl:grid-cols-2">
                {result.data.handoffs.map(item => (
                  <HandoffCard key={item.id} item={item} mutable={result.source === 'live'} onCreated={refresh} />
                ))}
              </div>
            ) : <p className="mt-4 rounded-lg border border-dashed border-indigo-200 bg-white p-5 text-center text-sm text-slate-600">当前没有待确认的 sales_handoff Task。</p>}
          </section>

          <section aria-labelledby="opportunity-board-heading" className="mt-6">
            <h2 id="opportunity-board-heading" className="sr-only">合格商机看板</h2>
            {result.data.opportunities.length ? (
              <div
                className="overflow-x-auto pb-3 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600"
                role="region"
                aria-label="合格商机看板，可横向滚动"
                tabIndex={0}
              >
                <div className="grid min-w-[1320px] grid-cols-6 gap-3">
                  {stages.map(([stage, label]) => {
                    const items = result.data.opportunities.filter(item => item.stage === stage);
                    return (
                      <section key={stage} aria-labelledby={`stage-${stage}`} className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                        <div className="flex items-center justify-between gap-2">
                          <h3 id={`stage-${stage}`} className="text-sm font-semibold text-slate-950">{label}</h3>
                          <span className="rounded-full bg-white px-2 py-0.5 text-xs text-slate-600">{items.length}</span>
                        </div>
                        <div className="mt-3 space-y-3">
                          {items.map(item => <OpportunityCard key={`${item.id}:${item.stage}`} item={item} mutable={result.source === 'live'} onUpdated={refresh} />)}
                        </div>
                      </section>
                    );
                  })}
                </div>
              </div>
            ) : <EmptyState title="尚无合格商机" detail="正向信号会先进入 sales_handoff Task；人工确认后才出现在这里。" />}
          </section>
        </>
      )}
    </ProductPageShell>
  );
}
