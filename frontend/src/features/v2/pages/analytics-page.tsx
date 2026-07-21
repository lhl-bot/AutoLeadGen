'use client';

import { ArrowRight, Coins, Gauge } from 'lucide-react';
import { v2Api } from '../api';
import { useV2Query } from '../use-v2-query';
import { EmptyState, LoadingState, MetricGrid, ProductPageShell, QueryErrorState, SourceBanner } from '../components/product-ui';

export default function AnalyticsPage() {
  const { result, loading, error, refresh } = useV2Query(v2Api.analytics);
  return (
    <ProductPageShell eyebrow="Outcomes & ROI" title="分析" description="合格商机是北极星；触达量、回复率、发送质量与 Provider 用量用于诊断，并保留 Campaign、Enrollment 与实体归因。">
      {error ? <QueryErrorState error={error} onRetry={refresh} /> : loading || !result ? <LoadingState label="正在读取 outcomes 与 Provider 账本…" /> : (
        <><SourceBanner envelope={result} onRefresh={refresh} /><MetricGrid metrics={result.data.outcomes} />
          <div className="grid gap-6 lg:grid-cols-2">
            <section aria-labelledby="funnel-heading" className="min-w-0 rounded-lg border border-slate-200 bg-white p-5 shadow-sm"><div className="flex items-center gap-2"><Gauge className="h-5 w-5 text-indigo-700" /><h2 id="funnel-heading" className="font-semibold text-slate-950">结果漏斗</h2></div><p className="mt-1 text-xs text-slate-500">正向信号 / 成功触达：{result.data.replyRate.toFixed(1)}%</p><ol className="mt-5 space-y-3">{result.data.funnel.map((stage, index) => <li key={stage.key} className="flex items-center gap-3"><div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-indigo-50 text-sm font-semibold text-indigo-800">{index + 1}</div><div className="min-w-0 flex-1 rounded-lg border border-slate-200 bg-slate-50 p-3"><div className="flex items-center justify-between gap-2"><span className="text-sm font-medium text-slate-900">{stage.label}</span><span className="font-semibold tabular-nums text-slate-950">{stage.count}</span></div></div>{index < result.data.funnel.length - 1 ? <ArrowRight className="h-4 w-4 shrink-0 text-slate-300" /> : <span className="w-4" />}</li>)}</ol></section>
            <section aria-labelledby="spend-heading" className="min-w-0 rounded-lg border border-slate-200 bg-white p-5 shadow-sm"><div className="flex items-center gap-2"><Coins className="h-5 w-5 text-indigo-700" /><h2 id="spend-heading" className="font-semibold text-slate-950">Spend & Provider efficiency</h2></div><p className="mt-1 text-xs text-slate-500">无价格版本时不伪造货币金额</p>{result.data.spend.length ? <div className="mt-5 w-full max-w-full overflow-x-auto focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600" role="region" aria-label="Provider 效率表格，可横向滚动" tabIndex={0}><table className="w-full min-w-[480px] text-left text-sm"><thead><tr className="text-xs uppercase text-slate-500"><th className="py-2">Provider</th><th className="py-2">用量</th><th className="py-2">结果</th><th className="py-2">效率</th></tr></thead><tbody className="divide-y divide-slate-200">{result.data.spend.map(item => <tr key={item.key}><td className="py-3 font-medium text-slate-900">{item.label}</td><td className="py-3 tabular-nums text-slate-700">{item.units} {item.unit}</td><td className="py-3 tabular-nums text-slate-700">{item.results}</td><td className="py-3 tabular-nums text-slate-700">{item.units ? (item.results / item.units).toFixed(2) : '—'}</td></tr>)}</tbody></table></div> : <div className="mt-5"><EmptyState title="尚无 ProviderCostEvent" detail="每个 fake/真实 Provider 调用都必须先进入事务化成本账本。" /></div>}{result.data.normalizedSpend.length ? <div className="mt-4 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-xs text-emerald-900">标准化成本：{result.data.normalizedSpend.map(item => `${item.currency} ${item.amount.toFixed(2)}`).join(' · ')}</div> : null}</section>
          </div>
        </>
      )}
    </ProductPageShell>
  );
}
