"use client";

import { useCallback, useEffect, useState } from 'react';
import {
  Activity,
  AlertCircle,
  CheckCircle2,
  CircleSlash,
  Database,
  ExternalLink,
  Gauge,
  Key,
  RefreshCw,
  ServerCog,
  WalletCards,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { apiFetch, cn } from '@/lib/utils';
import { useTranslation } from '@/lib/i18n';
import type { ApiUsageProvider, ApiUsageSummary } from '@/lib/types';

function formatCount(value: number | null | undefined) {
  return new Intl.NumberFormat().format(Number(value || 0));
}

function humanizeKey(key: string) {
  return key
    .replace(/_/g, ' ')
    .replace(/\b\w/g, char => char.toUpperCase());
}

function formatDetailValue(value: string | number | boolean | null | undefined) {
  if (value === null || value === undefined || value === '') return null;
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (typeof value === 'number') return formatCount(value);
  return String(value);
}

export default function ApiUsagePage() {
  const { language } = useTranslation();
  const txt = (en: string, zh: string) => (language === 'zh' ? zh : en);
  const [summary, setSummary] = useState<ApiUsageSummary | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchSummary = useCallback(async (refresh = false) => {
    if (refresh) setIsRefreshing(true);
    else setIsLoading(true);
    setError(null);

    try {
      const res = await apiFetch('/api/api-usage/summary');

      if (res.status === 403) {
        setError(language === 'zh' ? '只有管理员可以查看接口余额和全局用量。' : 'Only admins can view API balances and global usage.');
        return;
      }
      if (!res.ok) {
        setError(`${language === 'zh' ? '加载失败' : 'Failed to load'} (${res.status})`);
        return;
      }
      setSummary(await res.json());
    } catch (e) {
      console.error(e);
      setError(language === 'zh' ? '网络或供应商接口暂时不可用。' : 'Network or provider API is temporarily unavailable.');
    } finally {
      setIsLoading(false);
      setIsRefreshing(false);
    }
  }, [language]);

  useEffect(() => {
    fetchSummary();
  }, [fetchSummary]);

  const statusMeta: Record<ApiUsageProvider['status'], {
    label: string;
    className: string;
    icon: typeof CheckCircle2;
  }> = {
    ok: {
      label: txt('OK', '正常'),
      className: 'border-emerald-200 bg-emerald-50 text-emerald-700',
      icon: CheckCircle2,
    },
    warning: {
      label: txt('Needs attention', '需关注'),
      className: 'border-amber-200 bg-amber-50 text-amber-700',
      icon: AlertCircle,
    },
    missing: {
      label: txt('Not configured', '未配置'),
      className: 'border-slate-200 bg-slate-50 text-slate-500',
      icon: CircleSlash,
    },
    error: {
      label: txt('Error', '错误'),
      className: 'border-rose-200 bg-rose-50 text-rose-700',
      icon: AlertCircle,
    },
  };

  const categoryLabels: Record<string, string> = {
    'Contact data': txt('Contact data', '联系人数据'),
    'Email enrichment': txt('Email enrichment', '邮箱补全'),
    Search: txt('Search', '搜索'),
    Omnichannel: txt('Omnichannel', '多渠道'),
    'AI generation': txt('AI generation', 'AI 生成'),
    Delivery: txt('Delivery', '发送通道'),
  };

  const localUsageLabels: Record<string, string> = {
    email_outbound: txt('Outbound emails', '外发邮件'),
    omnichannel_messages: txt('LinkedIn / WhatsApp messages', 'LinkedIn / WhatsApp 消息'),
    processed_domains: txt('Processed domains', '已处理域名'),
    search_leads: txt('Search-sourced leads', '搜索来源线索'),
    lead_briefs: txt('AI research briefs', 'AI 调研简报'),
    ai_drafts: txt('AI email drafts', 'AI 邮件草稿'),
    chat_messages: txt('AI chat prompts', 'AI 对话请求'),
  };

  const lastUpdated = summary?.updated_at
    ? new Intl.DateTimeFormat(language === 'zh' ? 'zh-CN' : 'en-US', {
      month: 'short',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    }).format(new Date(summary.updated_at))
    : '';

  const statCards = summary ? [
    {
      label: txt('Configured providers', '已配置接口'),
      value: summary.totals.configured_providers,
      icon: Key,
      className: 'text-indigo-600 bg-indigo-50 border-indigo-100',
    },
    {
      label: txt('Healthy providers', '正常接口'),
      value: summary.totals.ok_providers,
      icon: CheckCircle2,
      className: 'text-emerald-600 bg-emerald-50 border-emerald-100',
    },
    {
      label: txt('Known balances', '可查询余额'),
      value: summary.totals.known_balance_providers,
      icon: WalletCards,
      className: 'text-sky-600 bg-sky-50 border-sky-100',
    },
    {
      label: txt('Local events, 30d', '本地用量，30 天'),
      value: summary.totals.local_events_30d,
      icon: Activity,
      className: 'text-amber-600 bg-amber-50 border-amber-100',
    },
  ] : [];

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-indigo-600">
            {txt('Administration', '管理')}
          </p>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-950 sm:text-3xl">
            {txt('API Usage & Balances', '接口用量和余额')}
          </h1>
          <p className="mt-2 text-sm text-slate-500">
            {txt('Balances from providers that expose them, plus local usage for the last 30 days.', '显示可查询的供应商余额，并汇总最近 30 天本地使用记录。')}
          </p>
        </div>
        <Button
          type="button"
          variant="outline"
          onClick={() => fetchSummary(true)}
          disabled={isLoading || isRefreshing}
          className="gap-2"
        >
          <RefreshCw className={cn('h-4 w-4', isRefreshing && 'animate-spin')} />
          {txt('Refresh', '刷新')}
        </Button>
      </div>

      {error && (
        <div className="flex items-start gap-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <div>{error}</div>
        </div>
      )}

      {isLoading ? (
        <div className="flex min-h-[320px] items-center justify-center rounded-lg border border-slate-200 bg-white text-sm text-slate-500">
          <RefreshCw className="mr-2 h-4 w-4 animate-spin" />
          {txt('Loading API usage...', '正在加载接口用量...')}
        </div>
      ) : summary ? (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            {statCards.map(stat => {
              const Icon = stat.icon;
              return (
                <div key={stat.label} className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
                  <div className="mb-5 flex items-center justify-between gap-3">
                    <div className={cn('flex h-10 w-10 items-center justify-center rounded-lg border', stat.className)}>
                      <Icon className="h-5 w-5" />
                    </div>
                    {stat.label === txt('Configured providers', '已配置接口') && summary.totals.warning_providers > 0 && (
                      <Badge variant="outline" className="border-amber-200 bg-amber-50 text-amber-700">
                        {summary.totals.warning_providers} {txt('warnings', '需关注')}
                      </Badge>
                    )}
                  </div>
                  <div className="text-3xl font-semibold tracking-tight text-slate-950">{formatCount(stat.value)}</div>
                  <div className="mt-1 text-sm font-medium text-slate-500">{stat.label}</div>
                </div>
              );
            })}
          </div>

          <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_320px]">
            <div className="rounded-lg border border-slate-200 bg-white shadow-sm">
              <div className="flex flex-col gap-2 border-b border-slate-200 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <h2 className="flex items-center gap-2 text-base font-semibold text-slate-950">
                    <Gauge className="h-4 w-4 text-indigo-600" />
                    {txt('Provider Status', '接口状态')}
                  </h2>
                  <p className="mt-1 text-xs text-slate-500">
                    {txt('Last checked', '最近检查')}: {lastUpdated}
                  </p>
                </div>
                <Badge variant="outline" className="w-fit border-slate-200 bg-slate-50 text-slate-600">
                  {summary.window_days} {txt('day window', '天窗口')}
                </Badge>
              </div>

              <div className="overflow-x-auto">
                <table className="min-w-[920px] w-full text-left text-sm">
                  <thead className="border-b border-slate-200 bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                    <tr>
                      <th className="px-5 py-3 font-semibold">{txt('Provider', '供应商')}</th>
                      <th className="px-5 py-3 font-semibold">{txt('Status', '状态')}</th>
                      <th className="px-5 py-3 font-semibold">{txt('Balance / quota', '余额 / 配额')}</th>
                      <th className="px-5 py-3 font-semibold">{txt('Local usage', '本地用量')}</th>
                      <th className="px-5 py-3 font-semibold">{txt('Details', '详情')}</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {summary.providers.map(provider => {
                      const meta = statusMeta[provider.status] || statusMeta.warning;
                      const StatusIcon = meta.icon;
                      const details = Object.entries(provider.details || {})
                        .map(([key, value]) => [key, formatDetailValue(value)] as const)
                        .filter(([, value]) => value);

                      return (
                        <tr key={provider.key} className="align-top transition-colors hover:bg-slate-50/70">
                          <td className="px-5 py-4">
                            <div className="flex items-start gap-3">
                              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-slate-200 bg-slate-50 text-slate-600">
                                <ServerCog className="h-4 w-4" />
                              </div>
                              <div className="min-w-0">
                                <div className="flex items-center gap-2">
                                  <span className="font-semibold text-slate-950">{provider.name}</span>
                                  {provider.docs_url && (
                                    <a
                                      href={provider.docs_url}
                                      target="_blank"
                                      rel="noreferrer"
                                      title={txt('Open provider docs', '打开供应商文档')}
                                      className="inline-flex h-6 w-6 items-center justify-center rounded-md text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-700"
                                    >
                                      <ExternalLink className="h-3.5 w-3.5" />
                                    </a>
                                  )}
                                </div>
                                <div className="mt-1 text-xs text-slate-500">
                                  {categoryLabels[provider.category] || provider.category}
                                </div>
                              </div>
                            </div>
                          </td>
                          <td className="px-5 py-4">
                            <Badge variant="outline" className={cn('gap-1.5', meta.className)}>
                              <StatusIcon className="h-3 w-3" />
                              {meta.label}
                            </Badge>
                            {provider.error && (
                              <div className="mt-2 max-w-[220px] text-xs leading-relaxed text-amber-700">
                                {provider.error}
                              </div>
                            )}
                          </td>
                          <td className="px-5 py-4">
                            <div className="font-semibold text-slate-950">{provider.balance_label}</div>
                            {provider.balance_unit && (
                              <div className="mt-1 text-xs text-slate-500">{provider.balance_unit}</div>
                            )}
                          </td>
                          <td className="px-5 py-4">
                            <div className="font-semibold text-slate-950">{formatCount(provider.usage_30d)}</div>
                            <div className="mt-1 text-xs text-slate-500">{provider.usage_label}</div>
                          </td>
                          <td className="px-5 py-4">
                            {details.length > 0 ? (
                              <div className="flex max-w-[300px] flex-wrap gap-2">
                                {details.slice(0, 5).map(([key, value]) => (
                                  <span key={key} className="rounded-md border border-slate-200 bg-slate-50 px-2 py-1 text-xs text-slate-600">
                                    <span className="text-slate-400">{humanizeKey(key)}:</span> {value}
                                  </span>
                                ))}
                              </div>
                            ) : (
                              <span className="text-xs text-slate-400">{txt('No extra details', '暂无更多详情')}</span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>

            <aside className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
              <div className="mb-4 flex items-center gap-2">
                <Database className="h-4 w-4 text-emerald-600" />
                <h2 className="text-base font-semibold text-slate-950">{txt('Local Usage', '本地用量')}</h2>
              </div>
              <div className="space-y-3">
                {summary.local_usage.map(item => (
                  <div key={item.key} className="flex items-center justify-between gap-4 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2.5">
                    <div className="min-w-0 text-sm text-slate-600">
                      {localUsageLabels[item.key] || item.label}
                    </div>
                    <div className="shrink-0 text-sm font-semibold text-slate-950">{formatCount(item.count)}</div>
                  </div>
                ))}
              </div>
              <div className="mt-4 rounded-lg border border-sky-200 bg-sky-50 px-3 py-2.5 text-xs leading-relaxed text-sky-800">
                {txt('Provider balances are queried live when their APIs support it. Local usage is counted from AutoLeadGen records.', '支持余额接口的供应商会实时查询；本地用量来自 AutoLeadGen 自己的记录。')}
              </div>
            </aside>
          </div>
        </>
      ) : (
        <div className="flex min-h-[260px] items-center justify-center rounded-lg border border-dashed border-slate-300 bg-white text-sm text-slate-500">
          {txt('No usage data available.', '暂无接口用量数据。')}
        </div>
      )}
    </div>
  );
}
