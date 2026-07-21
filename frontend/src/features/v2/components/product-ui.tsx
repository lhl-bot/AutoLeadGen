'use client';

import Link from 'next/link';
import { AlertTriangle, CheckCircle2, CircleHelp, Clock3, RefreshCw, ServerOff } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import type { DataEnvelope, OutcomeMetric, RuntimeSnapshot, RuntimeState } from '../types';

export function ProductPageShell({
  eyebrow,
  title,
  description,
  children,
}: {
  eyebrow: string;
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-6">
      <header className="max-w-3xl">
        <p className="text-xs font-semibold uppercase tracking-[0.16em] text-indigo-700">{eyebrow}</p>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight text-slate-950 sm:text-3xl">{title}</h1>
        <p className="mt-2 text-sm leading-6 text-slate-600">{description}</p>
      </header>
      {children}
    </div>
  );
}

export function SourceBanner<T>({ envelope, onRefresh }: { envelope: DataEnvelope<T>; onRefresh?: () => void }) {
  const live = envelope.source === 'live';
  const mixed = envelope.source === 'mixed';
  const sourceLabel = live ? 'V2 实时数据' : mixed ? '混合数据' : '示例数据';
  return (
    <div
      role="status"
      className={cn(
        'flex flex-col gap-3 rounded-lg border px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between',
        live ? 'border-slate-200 bg-white text-slate-700' : 'border-amber-300 bg-amber-50 text-amber-950',
      )}
    >
      <div className="flex min-w-0 items-start gap-2">
        {live ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-700" /> : <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-700" />}
        <div>
          <span className="font-semibold">{sourceLabel}</span>
          <span className="ml-2 text-xs text-slate-600">观测于 {formatDate(envelope.observedAt)}</span>
          {envelope.warning ? <p className="mt-1 text-xs leading-5">{envelope.warning}</p> : null}
        </div>
      </div>
      {onRefresh ? (
        <Button variant="outline" size="sm" onClick={onRefresh} className="min-h-11 shrink-0 bg-white">
          <RefreshCw className="mr-2 h-4 w-4" />刷新
        </Button>
      ) : null}
    </div>
  );
}

export function LoadingState({ label = '正在读取 V2 数据…' }: { label?: string }) {
  return (
    <div role="status" className="flex min-h-48 items-center justify-center rounded-lg border border-slate-200 bg-white text-sm text-slate-600">
      <RefreshCw className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" />{label}
    </div>
  );
}

export function QueryErrorState({ error, onRetry }: { error: Error; onRetry: () => void }) {
  return (
    <div role="alert" className="rounded-lg border border-rose-200 bg-rose-50 px-5 py-8 text-center text-rose-950">
      <ServerOff className="mx-auto h-7 w-7 text-rose-700" />
      <h2 className="mt-3 text-base font-semibold">暂时无法读取正式账号数据</h2>
      <p className="mx-auto mt-2 max-w-xl text-sm leading-6 text-rose-800">{error.message}。系统没有混入示例客户或示例发送记录。</p>
      <Button type="button" variant="outline" onClick={onRetry} className="mt-4 min-h-11 border-rose-300 bg-white">
        <RefreshCw className="h-4 w-4" />重试
      </Button>
    </div>
  );
}

export function EmptyState({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 px-5 py-10 text-center">
      <CircleHelp className="mx-auto h-6 w-6 text-slate-400" />
      <h2 className="mt-3 text-sm font-semibold text-slate-900">{title}</h2>
      <p className="mx-auto mt-1 max-w-xl text-sm text-slate-600">{detail}</p>
    </div>
  );
}

const stateStyle: Record<RuntimeState, string> = {
  running: 'border-emerald-200 bg-emerald-50 text-emerald-800',
  idle: 'border-sky-200 bg-sky-50 text-sky-800',
  backoff: 'border-amber-200 bg-amber-50 text-amber-900',
  blocked: 'border-amber-300 bg-amber-50 text-amber-900',
  failed: 'border-rose-300 bg-rose-50 text-rose-800',
  disabled: 'border-slate-200 bg-slate-100 text-slate-600',
  offline: 'border-rose-300 bg-rose-50 text-rose-800',
  unknown: 'border-slate-300 bg-white text-slate-700',
};

const stateLabel: Record<RuntimeState, string> = {
  running: '运行中',
  idle: '空闲',
  backoff: '退避',
  blocked: '阻塞',
  failed: '失败',
  disabled: '禁用',
  offline: '离线',
  unknown: '未知',
};

export function StatusPill({ state }: { state: RuntimeState }) {
  return <span className={cn('inline-flex rounded-full border px-2.5 py-1 text-xs font-semibold', stateStyle[state])}>{stateLabel[state]}</span>;
}

export function RuntimeGrid({ runtime, compact = false }: { runtime: RuntimeSnapshot; compact?: boolean }) {
  return (
    <section aria-labelledby="runtime-heading" className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 id="runtime-heading" className="text-base font-semibold text-slate-950">执行器状态</h2>
          <p className="mt-1 text-xs text-slate-500">仅来自持久化 heartbeat 与 lease</p>
        </div>
        <span className="text-xs text-slate-500">{runtime.services.filter(service => service.state === 'running' || service.state === 'idle').length}/{runtime.services.length} 可用</span>
      </div>
      <ul className={cn('mt-4 grid gap-3', compact ? 'grid-cols-1' : 'sm:grid-cols-2 xl:grid-cols-5')}>
        {runtime.services.map(service => (
          <li key={service.id} className="rounded-lg border border-slate-200 bg-slate-50 p-3">
            <div className="flex items-center justify-between gap-2">
              <span className="text-sm font-medium text-slate-900">{service.label}</span>
              <StatusPill state={service.state} />
            </div>
            <p className="mt-2 truncate text-xs text-slate-500" title={service.detail}>{service.detail}</p>
            {service.lastSeenAt ? <p className="mt-1 flex items-center gap-1 text-[11px] text-slate-500"><Clock3 className="h-3 w-3" />{formatDate(service.lastSeenAt)}</p> : null}
          </li>
        ))}
      </ul>
    </section>
  );
}

export function RuntimeSummary({ runtime }: { runtime: RuntimeSnapshot }) {
  const failing = runtime.services.filter(service => ['blocked', 'failed', 'offline'].includes(service.state));
  const degraded = runtime.services.filter(service => ['backoff', 'disabled'].includes(service.state));
  const unknown = runtime.services.filter(service => service.state === 'unknown');
  const label = failing.length
    ? `${failing.length} 个执行器异常`
    : degraded.length
      ? `${degraded.length} 个执行器退避或禁用`
      : unknown.length
        ? `${unknown.length} 个执行器无心跳`
        : '执行器心跳正常';
  const state: RuntimeState = failing.length ? 'failed' : degraded.length ? 'backoff' : unknown.length ? 'unknown' : 'running';
  const indicatorClass = state === 'running' ? 'bg-emerald-600' : state === 'backoff' ? 'bg-amber-500' : 'bg-slate-400';
  return (
    <div className="flex min-w-0 items-center gap-2" aria-label={`系统状态：${label}`}>
      {state === 'failed' ? <ServerOff className="h-4 w-4 text-rose-600" /> : <span className={cn('h-2.5 w-2.5 rounded-full', indicatorClass)} aria-hidden="true" />}
      <span className="truncate text-xs font-medium text-slate-700">{label}</span>
    </div>
  );
}

export function MetricGrid({ metrics }: { metrics: OutcomeMetric[] }) {
  return (
    <section aria-label="核心指标" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      {metrics.map(metric => (
        <article key={metric.key} className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
          <p className="text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">{metric.label}</p>
          <p className="mt-3 text-3xl font-semibold tabular-nums text-slate-950">{metric.value}</p>
          <p className="mt-2 text-xs text-slate-500">{metric.detail}</p>
        </article>
      ))}
    </section>
  );
}

export function ReadinessList({ checks }: { checks: Array<{ key: string; label: string; severity: string; passed: boolean; detail: string; remediationHref?: string }> }) {
  if (!checks.length) return <p className="text-sm text-slate-500">尚无就绪检查结果。</p>;
  return (
    <ul className="space-y-2">
      {checks.map(check => (
        <li key={check.key} className="flex items-start gap-3 rounded-lg border border-slate-200 bg-white p-3">
          {check.passed ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-700" /> : <AlertTriangle className={cn('mt-0.5 h-4 w-4 shrink-0', check.severity === 'blocker' ? 'text-rose-700' : 'text-amber-700')} />}
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-medium text-slate-900">{check.label}</span>
              <span className="text-[11px] uppercase text-slate-500">{check.severity}</span>
            </div>
            <p className="mt-1 text-xs leading-5 text-slate-600">{check.detail}</p>
          </div>
          {!check.passed && check.remediationHref ? <Link href={check.remediationHref} className="min-h-11 shrink-0 px-2 py-3 text-xs font-semibold text-indigo-700 hover:underline">整改</Link> : null}
        </li>
      ))}
    </ul>
  );
}

export function formatDate(value?: string) {
  if (!value) return '未设置';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
}
