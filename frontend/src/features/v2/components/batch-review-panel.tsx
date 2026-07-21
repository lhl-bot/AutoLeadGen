'use client';

import { useEffect, useMemo, useState } from 'react';
import { CheckCircle2, LoaderCircle, Pencil, RefreshCw, Sparkles, XCircle } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { v2Api, type ReviewBatchRead, type RouteProposalRead } from '../api';

type PreviewPayload = {
  company_name?: string;
  contact_name?: string;
  channel?: string;
  scheduled_at?: string;
  account_label?: string;
  subject?: string | null;
  body?: string;
  ai_reason?: string;
  evidence?: unknown[];
  estimated_cost?: number | string;
};

function routeLabel(route: RouteProposalRead) {
  const channel = route.steps.map(step => step.channel).join(' → ');
  return `客户 #${route.contact_id} · ${channel || '待人工处理'}`;
}

export function BatchReviewPanel() {
  const [proposals, setProposals] = useState<RouteProposalRead[]>([]);
  const [batches, setBatches] = useState<ReviewBatchRead[]>([]);
  const [selected, setSelected] = useState<number[]>([]);
  const [batch, setBatch] = useState<ReviewBatchRead | null>(null);
  const [approvalId, setApprovalId] = useState('');
  const [priceVersion, setPriceVersion] = useState('pilot-v1');
  const [busy, setBusy] = useState<string | null>(null);
  const [editingItemId, setEditingItemId] = useState<number | null>(null);
  const [editSubject, setEditSubject] = useState('');
  const [editBody, setEditBody] = useState('');

  const reload = async () => {
    const [proposalRows, batchRows] = await Promise.all([v2Api.routeProposals(), v2Api.reviewBatches()]);
    setProposals(proposalRows);
    setBatches(batchRows);
    const current = batchRows.find(item => item.status === 'previewed' || item.status === 'draft') ?? null;
    setBatch(current);
    if (current) {
      setSelected(current.items.map(item => item.route_proposal_id));
      setApprovalId(current.approval_id);
      setPriceVersion(current.price_version);
    }
  };

  useEffect(() => { void reload().catch(() => undefined); }, []);

  const available = useMemo(
    () => proposals.filter(item => item.status === 'draft' || item.status === 'previewed'),
    [proposals],
  );

  const toggle = (id: number) => {
    setSelected(current => current.includes(id)
      ? current.filter(item => item !== id)
      : current.length < 20 ? [...current, id] : current);
  };

  const preview = async () => {
    if (!selected.length) return toast.error('请至少选择一位客户');
    if (!approvalId.trim()) return toast.error('请输入本次审批编号');
    if (!priceVersion.trim()) return toast.error('请输入费用版本');
    setBusy('preview');
    try {
      const next = await v2Api.previewReviewBatch({ routeProposalIds: selected, approvalId: approvalId.trim(), priceVersion: priceVersion.trim(), batch: batch ?? undefined });
      setBatch(next);
      toast.success('批次预览已生成');
      await reload();
      setBatch(next);
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : '生成预览失败');
    } finally {
      setBusy(null);
    }
  };

  const saveEdit = async () => {
    if (!batch || editingItemId === null) return;
    setBusy(`edit-${editingItemId}`);
    try {
      const next = await v2Api.editReviewBatchItem(batch.id, editingItemId, { subject: editSubject || null, body: editBody });
      setBatch(next);
      setEditingItemId(null);
      toast.info('内容已保存，请重新预览后再批准');
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : '保存失败');
    } finally {
      setBusy(null);
    }
  };

  const approve = async () => {
    if (!batch) return;
    setBusy('approve');
    try {
      const next = await v2Api.approveReviewBatch(batch);
      setBatch(next);
      setSelected([]);
      toast.success(`已批准 ${next.item_count} 位客户的发送路线`);
      await reload();
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : '批准失败，请重新预览');
    } finally {
      setBusy(null);
    }
  };

  const reject = async () => {
    if (!batch) return;
    setBusy('reject');
    try {
      await v2Api.rejectReviewBatch(batch.id, '销售人工拒绝');
      setBatch(null);
      setSelected([]);
      toast.success('本批次已拒绝');
      await reload();
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : '拒绝失败');
    } finally {
      setBusy(null);
    }
  };

  return (
    <section aria-labelledby="batch-review-heading" className="rounded-xl border border-indigo-200 bg-white p-5 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><div className="flex items-center gap-2"><Sparkles className="h-5 w-5 text-indigo-700" /><h2 id="batch-review-heading" className="font-semibold text-slate-950">客户触达批次</h2></div><p className="mt-1 text-sm text-slate-600">确认每位客户的渠道、时间和内容后再整批批准；批准后路线不会自行变化。</p></div>
        <Button type="button" variant="outline" className="min-h-11" disabled={Boolean(busy)} onClick={() => void reload()}><RefreshCw className="h-4 w-4" />刷新</Button>
      </div>

      {available.length ? <div className="mt-5 grid gap-4 lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)]">
        <div>
          <h3 className="text-sm font-semibold text-slate-900">选择待审批客户（最多 20 位）</h3>
          <div className="mt-2 max-h-64 space-y-2 overflow-y-auto rounded-lg border border-slate-200 p-2">{available.map(route => <label key={route.id} className="flex min-h-11 cursor-pointer items-start gap-3 rounded-md p-2 hover:bg-slate-50"><input type="checkbox" className="mt-1 h-4 w-4" checked={selected.includes(route.id)} onChange={() => toggle(route.id)} /><span><span className="block text-sm font-medium text-slate-900">{routeLabel(route)}</span><span className="mt-0.5 block text-xs text-slate-500">可信度 {Math.round(Number(route.confidence) * 100)}% · {route.ai_reason}</span></span></label>)}</div>
          <div className="mt-3 grid gap-3 sm:grid-cols-2"><div><Label htmlFor="batch-approval-id">审批编号</Label><Input id="batch-approval-id" className="mt-1 min-h-11" value={approvalId} onChange={event => setApprovalId(event.target.value)} placeholder="例如 PILOT-2026-001" /></div><div><Label htmlFor="batch-price-version">费用版本</Label><Input id="batch-price-version" className="mt-1 min-h-11" value={priceVersion} onChange={event => setPriceVersion(event.target.value)} /></div></div>
          <Button type="button" className="mt-3 min-h-11" disabled={Boolean(busy) || !selected.length} onClick={preview}>{busy === 'preview' ? <LoaderCircle className="h-4 w-4 animate-spin motion-reduce:animate-none" /> : <Sparkles className="h-4 w-4" />}生成批次预览</Button>
        </div>

        <div>
          <div className="flex items-center justify-between"><h3 className="text-sm font-semibold text-slate-900">发送前预览</h3>{batch ? <span className={`rounded-full px-2 py-1 text-xs font-semibold ${batch.preview_checksum ? 'bg-emerald-100 text-emerald-800' : 'bg-amber-100 text-amber-900'}`}>{batch.preview_checksum ? '可批准' : '需要重新预览'}</span> : null}</div>
          {batch?.items.length ? <div className="mt-2 max-h-[34rem] space-y-3 overflow-y-auto pr-1">{batch.items.map(item => {
            const data = item.preview_payload as PreviewPayload;
            const editing = editingItemId === item.id;
            return <article key={item.id} className="rounded-lg border border-slate-200 p-4">
              <div className="flex items-start justify-between gap-3"><div><h4 className="font-semibold text-slate-950">{data.contact_name || `客户 #${item.route_proposal_id}`}</h4><p className="text-xs text-slate-500">{data.company_name || '公司待确认'} · {data.channel || '渠道待确认'} · {data.scheduled_at ? new Date(data.scheduled_at).toLocaleString('zh-CN') : '时间待确认'}</p></div><Button type="button" variant="ghost" className="min-h-11" onClick={() => { setEditingItemId(item.id); setEditSubject(data.subject ?? ''); setEditBody(data.body ?? ''); }}><Pencil className="h-4 w-4" />编辑</Button></div>
              {editing ? <div className="mt-3 space-y-3"><div><Label htmlFor={`subject-${item.id}`}>主题</Label><Input id={`subject-${item.id}`} className="mt-1 min-h-11" value={editSubject} onChange={event => setEditSubject(event.target.value)} /></div><div><Label htmlFor={`body-${item.id}`}>正文</Label><Textarea id={`body-${item.id}`} className="mt-1 min-h-40" value={editBody} onChange={event => setEditBody(event.target.value)} /></div><div className="flex gap-2"><Button type="button" className="min-h-11" disabled={Boolean(busy)} onClick={saveEdit}>保存</Button><Button type="button" variant="outline" className="min-h-11" onClick={() => setEditingItemId(null)}>取消</Button></div></div> : <><p className="mt-3 whitespace-pre-wrap rounded-md bg-slate-50 p-3 text-sm leading-6 text-slate-800">{data.subject ? `${data.subject}\n\n` : ''}{data.body || '正文待确认'}</p><dl className="mt-3 grid gap-2 text-xs text-slate-600 sm:grid-cols-2"><div><dt className="font-semibold">发送账号</dt><dd>{data.account_label || '待确认'}</dd></div><div><dt className="font-semibold">预计费用</dt><dd>{data.estimated_cost ?? '0'}</dd></div><div className="sm:col-span-2"><dt className="font-semibold">选择理由与证据</dt><dd>{data.ai_reason || '无'} · {data.evidence?.length ?? 0} 条证据</dd></div></dl></>}
            </article>;
          })}</div> : <p className="mt-2 rounded-lg border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500">AI 准备好客户路线后会显示在这里。外发目前仍由暂停开关保护。</p>}
          {batch ? <div className="mt-4 flex flex-wrap items-center gap-2"><Button type="button" className="min-h-11" disabled={Boolean(busy) || !batch.preview_checksum} onClick={approve}>{busy === 'approve' ? <LoaderCircle className="h-4 w-4 animate-spin motion-reduce:animate-none" /> : <CheckCircle2 className="h-4 w-4" />}批准整个批次</Button><Button type="button" variant="outline" className="min-h-11 border-rose-200 text-rose-800 hover:bg-rose-50" disabled={Boolean(busy)} onClick={reject}><XCircle className="h-4 w-4" />拒绝批次</Button><span className="text-xs text-slate-500">{batch.item_count} 位客户 · 预计费用 {batch.estimated_cost}</span></div> : null}
        </div>
      </div> : <p className="mt-5 rounded-lg border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500">当前没有待审批的客户路线。完成客户验证并创建试跑计划后，系统会在这里准备批次。</p>}
      {batches.some(item => item.status === 'approved') ? <p className="mt-3 text-xs text-emerald-800">最近已有批准批次；执行器只会按批准时冻结的路线运行。</p> : null}
    </section>
  );
}
