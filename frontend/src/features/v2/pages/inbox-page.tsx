'use client';

import { useState } from 'react';
import { CheckCircle2, MessageSquareReply, Sparkles } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { isPositiveReplyIntent, v2Api } from '../api';
import type { Conversation, ReplyIntent } from '../types';
import { useV2Query } from '../use-v2-query';
import { EmptyState, LoadingState, ProductPageShell, QueryErrorState, SourceBanner, formatDate } from '../components/product-ui';

const intentOptions: Array<[ReplyIntent, string]> = [
  ['interested', '感兴趣'],
  ['more_info', '需要更多信息'],
  ['referral', '转介绍'],
  ['meeting', '愿意开会'],
  ['not_interested', '不感兴趣'],
  ['unsubscribe', '退订'],
  ['out_of_office', '自动回复 / 休假'],
  ['bounce', '退信'],
  ['other', '其他'],
];

function ConversationAssessmentCard({ conversation, mutable, onConfirmed }: { conversation: Conversation; mutable: boolean; onConfirmed: () => void }) {
  const assessment = conversation.assessment;
  const [intent, setIntent] = useState<ReplyIntent>(assessment?.intent ?? 'other');
  const [pending, setPending] = useState(false);
  const canConfirm = mutable && assessment?.status === 'proposed';
  const positive = isPositiveReplyIntent(intent);

  const confirm = async () => {
    if (!assessment || !canConfirm) return;
    setPending(true);
    try {
      await v2Api.confirmReplyAssessment(assessment.id, intent);
      toast.success(positive ? '已确认正向信号并生成销售交接任务' : '回复判断已确认');
      onConfirmed();
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : '回复判断确认失败');
    } finally {
      setPending(false);
    }
  };

  return (
    <li className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2"><MessageSquareReply className="h-4 w-4 text-indigo-700" /><h2 className="font-semibold text-slate-950">{conversation.contactName}</h2><span className="text-sm text-slate-500">@ {conversation.company}</span><span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-700">{conversation.channel}</span></div>
          <p className="mt-2 text-sm font-medium text-slate-800">{conversation.subject}</p>
          <p className="mt-2 line-clamp-3 whitespace-pre-wrap text-sm leading-6 text-slate-600">{conversation.snippet}</p>
        </div>
        <div className="shrink-0 text-xs text-slate-500">{formatDate(conversation.lastReplyAt)}</div>
      </div>
      <div className="mt-4 border-t border-slate-100 pt-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded-full border border-slate-200 px-2.5 py-1 text-xs font-semibold text-slate-700">{conversation.status}</span>
          {assessment ? <span className="rounded-full border border-indigo-200 bg-indigo-50 px-2.5 py-1 text-xs font-semibold text-indigo-800"><Sparkles className="mr-1 inline h-3 w-3" />AI 提议：{intentOptions.find(([value]) => value === assessment.intent)?.[1] ?? assessment.intent}</span> : <span className="text-xs text-slate-500">尚无回复判断</span>}
          {assessment?.confidence !== undefined ? <span className="text-xs text-slate-500">置信度 {Math.round(assessment.confidence * 100)}%</span> : null}
        </div>
        {assessment ? (
          <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-end">
            <label className="min-w-0 flex-1 text-xs font-semibold text-slate-700">
              人工确认意图
              <select
                value={intent}
                onChange={event => setIntent(event.target.value as ReplyIntent)}
                disabled={!canConfirm || pending}
                className="mt-1 min-h-11 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm text-slate-900 outline-none focus-visible:border-indigo-500 focus-visible:ring-2 focus-visible:ring-indigo-200 disabled:bg-slate-100"
              >
                {intentOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </label>
            <div className="sm:w-56">
              <p className={`mb-1 text-xs font-semibold ${positive ? 'text-emerald-700' : 'text-slate-600'}`}>{positive ? '将按正向信号处理' : '不会创建销售交接'}</p>
              <Button type="button" className="min-h-11 w-full" disabled={!canConfirm || pending} onClick={confirm}>
                <CheckCircle2 className="h-4 w-4" />{pending ? '确认中…' : assessment.status === 'confirmed' ? '已确认' : '人工确认'}
              </Button>
            </div>
          </div>
        ) : null}
        {assessment?.rationale ? <p className="mt-2 text-xs leading-5 text-slate-500">判断依据：{assessment.rationale}</p> : null}
      </div>
    </li>
  );
}

export default function InboxPage() {
  const { result, loading, error, refresh } = useV2Query(v2Api.inbox);
  return (
    <ProductPageShell eyebrow="Conversation-first" title="收件箱" description="先展示最新回复正文，再由 AI 提议分类；人工确认正向信号后才生成 sales_handoff，退订与硬限制永远优先。">
      {error ? <QueryErrorState error={error} onRetry={refresh} /> : loading || !result ? <LoadingState label="正在读取 Conversation…" /> : (
        <><SourceBanner envelope={result} onRefresh={refresh} />
          {result.data.length ? <ul className="space-y-3">{result.data.map(conversation => <ConversationAssessmentCard key={`${conversation.id}:${conversation.assessment?.id}:${conversation.assessment?.status}`} conversation={conversation} mutable={result.source !== 'sample'} onConfirmed={refresh} />)}</ul> : <EmptyState title="尚无 Conversation" detail="Inbox worker 使用持久化 UIDVALIDITY + last_uid 游标；新消息会写入不可变 MessageEvent。" />}
        </>
      )}
    </ProductPageShell>
  );
}
