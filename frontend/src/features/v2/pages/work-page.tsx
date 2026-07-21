'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { AlertTriangle, ArrowRight, Ban, CalendarClock, CheckCircle2, ClipboardCheck, Send, ShieldAlert } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { v2Api, type ActivationRead } from '../api';
import { useV2Query } from '../use-v2-query';
import { LoadingState, MetricGrid, ProductPageShell, QueryErrorState, ReadinessList, SourceBanner, formatDate } from '../components/product-ui';
import type { DraftReviewPreview, WorkTask } from '../types';
import { BatchReviewPanel } from '../components/batch-review-panel';

const priorityStyle = {
  urgent: 'bg-rose-100 text-rose-800',
  high: 'bg-amber-100 text-amber-900',
  normal: 'bg-sky-100 text-sky-800',
  low: 'bg-slate-100 text-slate-700',
};

const specializedTaskTypes = new Set<WorkTask['type']>(['handoff', 'reply', 'approval', 'readiness']);

export function canCompleteTaskInline(task: WorkTask): boolean {
  return !specializedTaskTypes.has(task.type);
}

interface ReviewDecision {
  taskId: string;
  taskTitle: string;
  action: 'approve' | 'dismiss';
  preview?: DraftReviewPreview;
}

function DraftReviewImpact({ preview }: { preview: DraftReviewPreview }) {
  return (
    <div className="mt-3 rounded-lg border border-indigo-200 bg-indigo-50/70 p-3" aria-label="发送影响预览">
      <dl className="grid gap-x-4 gap-y-2 text-xs sm:grid-cols-2">
        <div><dt className="font-semibold text-slate-700">发送任务</dt><dd className="mt-0.5 text-slate-950">#{preview.attemptId}</dd></div>
        <div><dt className="font-semibold text-slate-700">渠道</dt><dd className="mt-0.5 text-slate-950">{preview.channel}</dd></div>
        <div><dt className="font-semibold text-slate-700">收件人</dt><dd className="mt-0.5 break-all text-slate-950">{preview.recipient}</dd></div>
        <div><dt className="font-semibold text-slate-700">模板版本</dt><dd className="mt-0.5 text-slate-950">{preview.templateVersion ?? '未记录'}</dd></div>
      </dl>
      {preview.subject ? <div className="mt-3"><p className="text-xs font-semibold text-slate-700">主题</p><p className="mt-1 break-words text-sm text-slate-950">{preview.subject}</p></div> : null}
      <div className="mt-3">
        <p className="text-xs font-semibold text-slate-700">将发送的正文</p>
        <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-md border border-slate-200 bg-white p-3 font-sans text-sm leading-6 text-slate-900">{preview.body}</pre>
      </div>
    </div>
  );
}

export default function WorkPage() {
  const { result, loading, error, refresh } = useV2Query(v2Api.work);
  const [completingTask, setCompletingTask] = useState<string | null>(null);
  const [reviewDecision, setReviewDecision] = useState<ReviewDecision | null>(null);
  const [taskQuery, setTaskQuery] = useState('');
  const [taskFilter, setTaskFilter] = useState<'all' | WorkTask['type']>('all');
  const [activation, setActivation] = useState<ActivationRead | null>(null);

  useEffect(() => { void v2Api.activation?.().then(setActivation).catch(() => undefined); }, []);

  const completeTask = async (taskId: string) => {
    setCompletingTask(taskId);
    try {
      await v2Api.completeTask(taskId);
      toast.success('任务已完成');
      refresh();
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : '任务更新失败');
    } finally {
      setCompletingTask(null);
    }
  };

  const submitReviewDecision = async () => {
    if (!reviewDecision) return;
    setCompletingTask(reviewDecision.taskId);
    try {
      if (reviewDecision.action === 'approve') {
        if (!reviewDecision.preview?.body.trim()) throw new Error('邮件正文不能为空');
        await v2Api.approveTask(reviewDecision.taskId, {
          subject: reviewDecision.preview.subject,
          body: reviewDecision.preview.body,
        });
        toast.success('已批准，发送任务已重新排队');
      } else {
        await v2Api.dismissTask(reviewDecision.taskId);
        toast.success('已拒绝，后续发送已取消');
      }
      setReviewDecision(null);
      refresh();
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : '审批更新失败');
    } finally {
      setCompletingTask(null);
    }
  };

  const reviewWritesDisabled = !result || result.source !== 'live';
  const filteredTasks = useMemo(() => {
    if (!result) return [];
    const query = taskQuery.trim().toLocaleLowerCase();
    return result.data.tasks.filter(task => (
      (taskFilter === 'all' || task.type === taskFilter)
      && (!query || [task.title, task.detail, task.campaign ?? ''].some(value => value.toLocaleLowerCase().includes(query)))
    ));
  }, [result, taskFilter, taskQuery]);
  return (
    <ProductPageShell eyebrow="销售工作台" title="今日工作" description="先处理当前计划的批次审批、回复和销售交接；数据维护与系统检查由管理员处理。">
      {error ? <QueryErrorState error={error} onRetry={refresh} /> : loading || !result ? <LoadingState label="正在汇总今日工作…" /> : (
        <>
          <SourceBanner envelope={result} onRefresh={refresh} />
          {activation && !activation.activated ? (
            <section aria-labelledby="next-step-heading" className="rounded-xl border border-indigo-200 bg-indigo-50 p-5 sm:flex sm:items-center sm:justify-between sm:gap-5">
              <div className="flex items-start gap-3"><span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-indigo-700 text-sm font-bold text-white">{activation.current_step}</span><div><h2 id="next-step-heading" className="font-semibold text-slate-950">下一步：{activation.steps[activation.current_step - 1]?.label}</h2><p className="mt-1 text-sm text-slate-600">{activation.steps[activation.current_step - 1]?.detail}</p>{(activation.blockers ?? []).length ? <p className="mt-2 text-xs font-medium text-amber-900">当前阻断：{activation.blockers?.[0]}</p> : null}</div></div>
              <Link href={activation.steps[activation.current_step - 1]?.href ?? '/dashboard/get-started'} className="mt-4 inline-flex min-h-11 shrink-0 items-center justify-center rounded-lg bg-slate-950 px-4 text-sm font-semibold text-white sm:mt-0">继续首次触达<ArrowRight className="ml-2 h-4 w-4" /></Link>
            </section>
          ) : null}
          <MetricGrid metrics={result.data.metrics} />
          <BatchReviewPanel />
          <div className="grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
            <section aria-labelledby="tasks-heading" className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <h2 id="tasks-heading" className="text-base font-semibold text-slate-950">待办任务</h2>
                  <p className="mt-1 text-xs text-slate-500">仅显示当前计划的审批、回复和销售交接任务</p>
                </div>
                <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-semibold text-slate-700">{filteredTasks.length} / {result.data.tasks.length}</span>
              </div>
              <div className="mt-4 grid gap-3 sm:grid-cols-[1fr_190px]" aria-label="待办任务筛选">
                <label className="text-xs font-semibold text-slate-700">搜索任务<input aria-label="搜索待办任务" value={taskQuery} onChange={event => setTaskQuery(event.target.value)} placeholder="标题、说明或计划……" className="mt-1 min-h-11 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm font-normal text-slate-950 outline-none focus-visible:border-indigo-500 focus-visible:ring-2 focus-visible:ring-indigo-200" /></label>
                <label className="text-xs font-semibold text-slate-700">任务类型<select aria-label="筛选待办任务类型" value={taskFilter} onChange={event => setTaskFilter(event.target.value as typeof taskFilter)} className="mt-1 min-h-11 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm font-normal text-slate-950 outline-none focus-visible:border-indigo-500 focus-visible:ring-2 focus-visible:ring-indigo-200"><option value="all">全部类型</option><option value="data">客户资料</option><option value="exception">对账与异常</option><option value="reply">回复处理</option><option value="approval">发送审批</option><option value="handoff">销售交接</option><option value="readiness">启动检查</option><option value="budget">预算</option></select></label>
              </div>
              {filteredTasks.length ? (
                <ul className="mt-4 divide-y divide-slate-200">
                  {filteredTasks.map(task => (
                    <li key={task.id} className="py-4 first:pt-0 last:pb-0">
                      <div className="flex items-start gap-3">
                        {task.type === 'exception' ? <ShieldAlert className="mt-1 h-4 w-4 shrink-0 text-rose-700" /> : <ClipboardCheck className="mt-1 h-4 w-4 shrink-0 text-indigo-700" />}
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-2">
                            <h3 className="text-sm font-semibold text-slate-950">{task.title}</h3>
                            <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${priorityStyle[task.priority]}`}>{task.priority}</span>
                          </div>
                          <p className="mt-1 text-sm leading-6 text-slate-600">{task.detail}</p>
                          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500">
                            {task.campaign ? <span>计划：{task.campaign}</span> : null}
                            {task.dueAt ? <span className="flex items-center gap-1"><CalendarClock className="h-3 w-3" />{formatDate(task.dueAt)}</span> : null}
                          </div>
                          {task.type === 'approval' ? (
                            <div>
                              {task.draftReview ? <DraftReviewImpact preview={task.draftReview} /> : (
                                <p role="alert" className="mt-3 rounded-lg border border-rose-200 bg-rose-50 p-3 text-xs font-medium text-rose-800">
                                  该审批任务缺少渠道、收件人或正文预览，已禁止批准，请先对账。
                                </p>
                              )}
                              <div className="mt-3 flex flex-wrap gap-2">
                                <Button
                                  type="button"
                                  className="min-h-11"
                                  disabled={reviewWritesDisabled || !task.draftReview || completingTask === task.id}
                                  title={reviewWritesDisabled ? '示例或混合数据不可写入' : !task.draftReview ? '预览证据不完整' : undefined}
                                  onClick={() => task.draftReview && setReviewDecision({ taskId: task.id, taskTitle: task.title, action: 'approve', preview: task.draftReview })}
                                >
                                  <Send className="h-4 w-4" />批准发送
                                </Button>
                                <Button
                                  type="button"
                                  variant="outline"
                                  className="min-h-11 border-rose-200 text-rose-800 hover:bg-rose-50"
                                  disabled={reviewWritesDisabled || completingTask === task.id}
                                  title={reviewWritesDisabled ? '示例或混合数据不可写入' : undefined}
                                  onClick={() => setReviewDecision({ taskId: task.id, taskTitle: task.title, action: 'dismiss', preview: task.draftReview })}
                                >
                                  <Ban className="h-4 w-4" />拒绝
                                </Button>
                              </div>
                            </div>
                          ) : null}
                        </div>
                        <div className="flex shrink-0 flex-col items-stretch gap-1 sm:flex-row">
                          <Link href={task.href} className="inline-flex min-h-11 items-center justify-center px-2 text-xs font-semibold text-indigo-700 hover:underline">处理<ArrowRight className="ml-1 h-3 w-3" /></Link>
                          {canCompleteTaskInline(task) ? (
                            <Button
                              type="button"
                              variant="outline"
                              className="min-h-11"
                              disabled={result.source !== 'live' || completingTask === task.id}
                              onClick={() => completeTask(task.id)}
                              title={result.source !== 'live' ? '示例或混合数据不可写入' : undefined}
                            >
                              <CheckCircle2 className="h-4 w-4" />{completingTask === task.id ? '提交中…' : '完成'}
                            </Button>
                          ) : null}
                        </div>
                      </div>
                    </li>
                  ))}
                </ul>
              ) : <p className="mt-4 rounded-lg border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500">{result.data.tasks.length ? '没有匹配的开放任务。' : '当前没有开放任务。'}</p>}
            </section>
            <section aria-labelledby="readiness-heading" className="rounded-lg border border-slate-200 bg-slate-50 p-5">
              <div className="flex items-start gap-2">
                <AlertTriangle className="mt-0.5 h-4 w-4 text-amber-700" />
                <div>
                  <h2 id="readiness-heading" className="text-base font-semibold text-slate-950">计划就绪度</h2>
                  <p className="mt-1 text-xs text-slate-500">存在未通过检查时，计划不会启动</p>
                </div>
              </div>
              <div className="mt-4 space-y-4">
                {result.data.campaigns.map(campaign => (
                  <article key={campaign.id}>
                    <h3 className="mb-2 text-sm font-semibold text-slate-900">{campaign.name}</h3>
                    <ReadinessList checks={campaign.readiness.filter(check => !check.passed)} />
                  </article>
                ))}
                {!result.data.campaigns.length ? <p className="text-sm text-slate-500">尚无可运行的销售计划。</p> : null}
              </div>
            </section>
          </div>
          <Dialog open={reviewDecision !== null} onOpenChange={open => { if (!open && !completingTask) setReviewDecision(null); }}>
            <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-xl" showCloseButton={!completingTask}>
              <DialogHeader>
                <DialogTitle>{reviewDecision?.action === 'approve' ? '确认批准发送' : '确认拒绝草稿'}</DialogTitle>
                <DialogDescription>
                  {reviewDecision?.action === 'approve'
                    ? '批准后会保存这一版内容并重新排队；真正发送前仍会检查同意状态与渠道暂停开关。'
                    : '拒绝后会关闭该审批并取消对应的后续发送。'}
                </DialogDescription>
              </DialogHeader>
              {reviewDecision?.preview && reviewDecision.action === 'approve' ? (
                <div className="space-y-4 rounded-lg border border-indigo-200 bg-indigo-50/60 p-4">
                  <div>
                    <Label htmlFor="review-subject">主题</Label>
                    <Input
                      id="review-subject"
                      value={reviewDecision.preview.subject ?? ''}
                      onChange={event => setReviewDecision(current => current?.preview ? {
                        ...current,
                        preview: { ...current.preview, subject: event.target.value },
                      } : current)}
                      className="mt-1 min-h-11 bg-white"
                    />
                  </div>
                  <div>
                    <Label htmlFor="review-body">正文</Label>
                    <Textarea
                      id="review-body"
                      value={reviewDecision.preview.body}
                      onChange={event => setReviewDecision(current => current?.preview ? {
                        ...current,
                        preview: { ...current.preview, body: event.target.value },
                      } : current)}
                      className="mt-1 min-h-52 bg-white leading-6"
                    />
                  </div>
                  <p className="text-xs text-indigo-900">这份编辑后的快照会随批准结果保存；发送执行器只使用这一版。</p>
                </div>
              ) : reviewDecision?.preview ? <DraftReviewImpact preview={reviewDecision.preview} /> : reviewDecision ? (
                <p role="alert" className="rounded-lg border border-rose-200 bg-rose-50 p-3 text-xs font-medium text-rose-800">
                  该任务没有完整发送预览；仅允许拒绝并取消，不允许批准。
                </p>
              ) : null}
              {reviewDecision ? <p className="text-xs text-slate-500">任务：{reviewDecision.taskTitle}</p> : null}
              <DialogFooter>
                <Button type="button" variant="outline" disabled={Boolean(completingTask)} onClick={() => setReviewDecision(null)}>取消</Button>
                <Button
                  type="button"
                  variant={reviewDecision?.action === 'dismiss' ? 'outline' : 'default'}
                  className={reviewDecision?.action === 'dismiss' ? 'border-rose-200 text-rose-800 hover:bg-rose-50' : undefined}
                  disabled={Boolean(completingTask)}
                  onClick={submitReviewDecision}
                >
                  {completingTask ? '提交中…' : reviewDecision?.action === 'approve' ? '确认批准并重新入队' : '确认拒绝并取消'}
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </>
      )}
    </ProductPageShell>
  );
}
