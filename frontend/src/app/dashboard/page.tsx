"use client";

import { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { Bot, Mail, Zap, Search, Briefcase, MessageSquare, TrendingUp, Sparkles, ArrowRight } from 'lucide-react';
import { apiFetch } from '@/lib/utils';
import type { DashboardKpis, DashboardTrend, TodayReport } from '@/lib/types';
import ConversionFunnel from '@/components/ConversionFunnel';
import ActivityFeed from '@/components/ActivityFeed';
import OnboardingChecklist from '@/components/OnboardingChecklist';
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend
} from 'recharts';
import { useTranslation } from '@/lib/i18n';

export default function DashboardOverview() {
  const [stats, setStats] = useState<DashboardKpis>({
    active_workflows: 0,
    total_leads: 0,
    emails_sent: 0,
    total_replies: 0,
  });
  const { t } = useTranslation();
  const [trends, setTrends] = useState<DashboardTrend[]>([]);
  const [todayReport, setTodayReport] = useState<TodayReport | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [health, setHealth] = useState<{
    database: string;
    outbound_engine: string;
    outbound_engine_thread?: string;
    prospecting_engine_thread?: string;
    inbox_monitor_thread?: string;
    omnichannel_engine_thread?: string;
    unipile_status?: string;
    active_workflows: number;
    recent_emails_30m: number;
    unipile_accounts: number;
    email_accounts: number;
    has_active_email: boolean;
    llm_api_configured: boolean;
  } | null>(null);

  useEffect(() => {
    let cancelled = false;

    const fetchAnalytics = async () => {
      try {
        const res = await apiFetch('/api/analytics/dashboard');
        if (res.ok && !cancelled) {
          const data = await res.json();
          setStats(data.kpis);
          setTrends(data.trends);
          setTodayReport(data.today_report || null);
        }
      } catch (e) {
        console.error("Failed to load analytics", e);
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    };

    const fetchHealth = async () => {
      try {
        const hRes = await apiFetch('/api/health/status');
        if (hRes.ok && !cancelled) {
          setHealth(await hRes.json());
        }
      } catch (e) {
        console.error("Failed to load health", e);
      }
    };

    fetchAnalytics();
    const healthTimer = window.setTimeout(fetchHealth, 1000);

    return () => {
      cancelled = true;
      window.clearTimeout(healthTimer);
    };
  }, []);

  return (
    <div>
      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:justify-between sm:items-end">
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-indigo-600">{t('Overview')}</p>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-950 sm:text-3xl">{t('Welcome to AutoLeadGen')}</h1>
          <p className="mt-2 text-sm text-slate-500">{t('Your AI-powered outbound sales engine is ready.')}</p>
        </div>
      </div>

      <OnboardingChecklist />

      {isLoading ? (
        <div className="py-20 text-center text-gray-500">{t('Loading analytics...')}</div>
      ) : (
        <>
          {/* ── AI Daily Work Report ── */}
          {todayReport && (
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              className="glass-panel mb-6 overflow-hidden rounded-lg border border-indigo-200/70 p-5 sm:p-6"
            >
              <div className="relative z-10">
                <h2 className="mb-4 flex items-center gap-2 text-lg font-semibold text-slate-950">
                  <Sparkles className="w-5 h-5 text-indigo-400" />
                  {t("Today's AI Work Report")}
                  <span className="ml-2 text-xs font-normal text-slate-500">{t("Today's Report")}</span>
                </h2>
                <div className="grid sm:grid-cols-3 gap-4">
                  <div className="flex items-center gap-4 rounded-lg border border-emerald-200/70 bg-emerald-50/70 p-4">
                    <div className="p-2.5 bg-emerald-500/10 rounded-lg">
                      <Search className="w-6 h-6 text-emerald-400" />
                    </div>
                    <div>
                      <div className="text-2xl font-bold text-emerald-400">{todayReport.leads_found_today}</div>
                      <div className="text-xs text-slate-500">{t('High-value Leads Found')}</div>
                    </div>
                  </div>
                  <div className="flex items-center gap-4 rounded-lg border border-indigo-200/70 bg-indigo-50/70 p-4">
                    <div className="p-2.5 bg-indigo-500/10 rounded-lg">
                      <Mail className="w-6 h-6 text-indigo-400" />
                    </div>
                    <div>
                      <div className="text-2xl font-bold text-indigo-400">{todayReport.emails_sent_today}</div>
                      <div className="text-xs text-slate-500">{t('Emails Sent')}</div>
                    </div>
                  </div>
                  <div className="relative flex items-center gap-4 rounded-lg border border-amber-200/70 bg-amber-50/70 p-4">
                    {todayReport.high_intent_replies > 0 && (
                      <div className="absolute top-2 right-2 w-2.5 h-2.5 rounded-full bg-amber-400 animate-pulse" />
                    )}
                    <div className="p-2.5 bg-amber-500/10 rounded-lg">
                      <MessageSquare className="w-6 h-6 text-amber-400" />
                    </div>
                    <div className="flex-1">
                      <div className="text-2xl font-bold text-amber-400">{todayReport.high_intent_replies}</div>
                      <div className="text-xs text-slate-500">{t('High-intent Replies')}</div>
                    </div>
                    {todayReport.high_intent_replies > 0 && (
                      <a href="/dashboard/replies" className="flex items-center gap-1 text-xs text-amber-400 hover:text-amber-300 transition-colors">
                        {t('Follow up')} <ArrowRight className="w-3.5 h-3.5" />
                      </a>
                    )}
                  </div>
                </div>
                {todayReport.active_workflow_names.length > 0 && (
                  <div className="mt-3 flex items-center gap-2 text-xs text-slate-500">
                    <Bot className="w-3.5 h-3.5" />
                    {t('Active Workflows Running')}: {todayReport.active_workflow_names.join(' · ')}
                  </div>
                )}
              </div>
            </motion.div>
          )}

          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4 mb-6">
            {[
              { label: t("Active Workflows"), value: stats.active_workflows, icon: Briefcase, color: "text-indigo-500" },
              { label: t("Leads Sourced"), value: stats.total_leads, icon: Search, color: "text-emerald-500" },
              { label: t("Messages Sent"), value: stats.emails_sent, icon: Mail, color: "text-orange-500" },
              { label: t("Total Replies"), value: stats.total_replies, icon: MessageSquare, color: "text-purple-500" }
            ].map((stat, i) => (
              <motion.div
                key={i}
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.1 }}
                className="glass-panel group relative overflow-hidden rounded-lg border border-white/5 p-5 transition-transform hover:-translate-y-0.5"
              >
                <div className="flex justify-between items-start mb-5">
                  <div className="rounded-lg bg-slate-50 p-2.5 ring-1 ring-slate-200/70">
                    <stat.icon className={`w-6 h-6 ${stat.color}`} />
                  </div>
                </div>
                <div className="text-3xl font-semibold tracking-tight text-slate-900 mb-1">{stat.value}</div>
                <div className="text-sm font-medium uppercase tracking-wider text-slate-500">{stat.label}</div>
              </motion.div>
            ))}
          </div>

          <div className="grid gap-4 lg:grid-cols-2 mb-6">
            <ConversionFunnel />
            <ActivityFeed />
          </div>

          <div className="grid lg:grid-cols-3 gap-4 mb-8">
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ delay: 0.3 }}
                className="glass-panel rounded-lg border border-white/5 p-5 sm:p-6 lg:col-span-2"
              >
              <h2 className="mb-6 flex items-center gap-2 text-lg font-semibold text-slate-950">
                <TrendingUp className="w-5 h-5 text-indigo-500" /> {t('Performance Trends (14 Days)')}
              </h2>
              {trends.length > 0 && (
              <div className="h-[300px] w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={trends} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                    <defs>
                      <linearGradient id="colorLeads" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#34d399" stopOpacity={0.3}/>
                        <stop offset="95%" stopColor="#34d399" stopOpacity={0}/>
                      </linearGradient>
                      <linearGradient id="colorEmails" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#818cf8" stopOpacity={0.3}/>
                        <stop offset="95%" stopColor="#818cf8" stopOpacity={0}/>
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(100,116,139,0.18)" vertical={false} />
                    <XAxis dataKey="date" stroke="#64748b" fontSize={12} tickLine={false} axisLine={false} />
                    <YAxis stroke="#64748b" fontSize={12} tickLine={false} axisLine={false} />
                    <Tooltip 
                      contentStyle={{ backgroundColor: '#ffffff', border: '1px solid rgba(15,23,42,0.1)', borderRadius: '8px', color: '#0f172a', boxShadow: '0 12px 30px rgba(15,23,42,0.1)' }}
                      itemStyle={{ color: '#0f172a' }}
                    />
                    <Legend />
                    <Area type="monotone" name={t("Leads Sourced")} dataKey="leads_found" stroke="#34d399" strokeWidth={2} fillOpacity={1} fill="url(#colorLeads)" />
                    <Area type="monotone" name={t("Emails Sent")} dataKey="emails_sent" stroke="#818cf8" strokeWidth={2} fillOpacity={1} fill="url(#colorEmails)" />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
              )}
              {trends.length === 0 && (
                <div className="flex h-[300px] items-center justify-center rounded-lg border border-dashed border-slate-200 bg-slate-50/70 text-sm text-slate-500">
                  {t('No data')}
                </div>
              )}
            </motion.div>

            <div className="self-start">
              <motion.div
                initial={{ opacity: 0, scale: 0.95 }}
                animate={{ opacity: 1, scale: 1 }}
                transition={{ delay: 0.4 }}
                className="glass-panel relative flex flex-col overflow-hidden rounded-lg border border-white/5 p-5 sm:p-6"
              >
              <div>
                <h2 className="mb-4 flex items-center gap-2 text-lg font-semibold text-slate-950">
                  <Bot className="w-5 h-5 text-emerald-500" /> {t('System Status')}
                </h2>
                <div className="space-y-3 relative z-10">
                  {/* 1. Database Connection */}
                  <div className="p-3 bg-black/40 rounded-lg border border-white/5 flex justify-between items-center gap-3">
                    <div>
                      <div className="font-semibold text-slate-900">{t('Database Connection')}</div>
                      <div className="text-xs text-gray-400">{t('MySQL Enterprise DB')}</div>
                    </div>
                    <div className={`flex items-center gap-1.5 text-xs font-semibold px-2.5 py-0.5 rounded-full ${
                      health?.database === 'online' ? 'text-emerald-500 bg-emerald-500/10' : 'text-rose-500 bg-rose-500/10'
                    }`}>
                      <div className={`w-2 h-2 rounded-full ${health?.database === 'online' ? 'bg-emerald-400 animate-pulse' : 'bg-rose-400'}`}></div>
                      {health?.database === 'online' ? t('Online') : t('Offline')}
                    </div>
                  </div>

                  {/* 2. Outbound Engine */}
                  <div className="p-3 bg-black/40 rounded-lg border border-white/5 flex justify-between items-center gap-3">
                    <div>
                      <div className="font-semibold text-slate-900">{t('Outbound Engine')}</div>
                      <div className="text-xs text-gray-400">{health ? `${health.active_workflows} active · ${health.recent_emails_30m} sent/30m` : t('Background workers')}</div>
                    </div>
                    <div className={`flex items-center gap-1.5 text-xs font-semibold px-2.5 py-0.5 rounded-full ${
                      health?.outbound_engine_thread === 'running'
                        ? health?.outbound_engine === 'active' ? 'text-emerald-500 bg-emerald-500/10' : 'text-amber-500 bg-amber-500/10'
                        : health?.outbound_engine_thread === 'disabled' ? 'text-gray-500 bg-gray-500/10' : 'text-rose-500 bg-rose-500/10'
                    }`}>
                      <div className={`w-2 h-2 rounded-full ${
                        health?.outbound_engine_thread === 'running'
                          ? health?.outbound_engine === 'active' ? 'bg-emerald-400 animate-pulse' : 'bg-amber-400'
                          : health?.outbound_engine_thread === 'disabled' ? 'bg-gray-400' : 'bg-rose-400'
                      }`}></div>
                      {health?.outbound_engine_thread === 'running'
                        ? health?.outbound_engine === 'active' ? t('Online') : t('Idle')
                        : health?.outbound_engine_thread === 'disabled' ? t('Disabled') : t('Offline')
                      }
                    </div>
                  </div>

                  {/* 3. Prospecting Engine */}
                  <div className="p-3 bg-black/40 rounded-lg border border-white/5 flex justify-between items-center gap-3">
                    <div>
                      <div className="font-semibold text-slate-900">{t('Prospecting Engine')}</div>
                      <div className="text-xs text-gray-400">{t('Automated lead sourcing')}</div>
                    </div>
                    <div className={`flex items-center gap-1.5 text-xs font-semibold px-2.5 py-0.5 rounded-full ${
                      health?.prospecting_engine_thread === 'running'
                        ? 'text-emerald-500 bg-emerald-500/10'
                        : health?.prospecting_engine_thread === 'disabled' ? 'text-gray-500 bg-gray-500/10' : 'text-rose-500 bg-rose-500/10'
                    }`}>
                      <div className={`w-2 h-2 rounded-full ${
                        health?.prospecting_engine_thread === 'running'
                          ? 'bg-emerald-400 animate-pulse'
                          : health?.prospecting_engine_thread === 'disabled' ? 'bg-gray-400' : 'bg-rose-400'
                      }`}></div>
                      {health?.prospecting_engine_thread === 'running'
                        ? t('Online')
                        : health?.prospecting_engine_thread === 'disabled' ? t('Disabled') : t('Offline')
                      }
                    </div>
                  </div>

                  {/* 4. Inbox Monitor */}
                  <div className="p-3 bg-black/40 rounded-lg border border-white/5 flex justify-between items-center gap-3">
                    <div>
                      <div className="font-semibold text-slate-900">{t('Inbox Monitor')}</div>
                      <div className="text-xs text-gray-400">{t('Reply detection daemon')}</div>
                    </div>
                    <div className={`flex items-center gap-1.5 text-xs font-semibold px-2.5 py-0.5 rounded-full ${
                      health?.inbox_monitor_thread === 'running'
                        ? 'text-emerald-500 bg-emerald-500/10'
                        : health?.inbox_monitor_thread === 'disabled' ? 'text-gray-500 bg-gray-500/10' : 'text-rose-500 bg-rose-500/10'
                    }`}>
                      <div className={`w-2 h-2 rounded-full ${
                        health?.inbox_monitor_thread === 'running'
                          ? 'bg-emerald-400 animate-pulse'
                          : health?.inbox_monitor_thread === 'disabled' ? 'bg-gray-400' : 'bg-rose-400'
                      }`}></div>
                      {health?.inbox_monitor_thread === 'running'
                        ? t('Online')
                        : health?.inbox_monitor_thread === 'disabled' ? t('Disabled') : t('Offline')
                      }
                    </div>
                  </div>

                  {/* 5. Omnichannel Router */}
                  <div className="p-3 bg-black/40 rounded-lg border border-white/5 flex justify-between items-center gap-3">
                    <div>
                      <div className="font-semibold text-slate-900">{t('Omnichannel Router')}</div>
                      <div className="text-xs text-gray-400">{t('WhatsApp/LinkedIn router')}</div>
                    </div>
                    <div className={`flex items-center gap-1.5 text-xs font-semibold px-2.5 py-0.5 rounded-full ${
                      health?.omnichannel_engine_thread === 'running'
                        ? 'text-emerald-500 bg-emerald-500/10'
                        : health?.omnichannel_engine_thread === 'disabled' ? 'text-gray-500 bg-gray-500/10' : 'text-rose-500 bg-rose-500/10'
                    }`}>
                      <div className={`w-2 h-2 rounded-full ${
                        health?.omnichannel_engine_thread === 'running'
                          ? 'bg-emerald-400 animate-pulse'
                          : health?.omnichannel_engine_thread === 'disabled' ? 'bg-gray-400' : 'bg-rose-400'
                      }`}></div>
                      {health?.omnichannel_engine_thread === 'running'
                        ? t('Online')
                        : health?.omnichannel_engine_thread === 'disabled' ? t('Disabled') : t('Offline')
                      }
                    </div>
                  </div>

                  {/* 6. Research Agent */}
                  <div className="p-3 bg-black/40 rounded-lg border border-white/5 flex justify-between items-center gap-3">
                    <div>
                      <div className="font-semibold text-slate-900">{t('Research Agent')}</div>
                      <div className="text-xs text-gray-400">{health?.llm_api_configured ? t('DeepSeek LLM Models') : 'LLM not configured'}</div>
                    </div>
                    <div className={`flex items-center gap-1.5 text-xs font-semibold px-2.5 py-0.5 rounded-full ${
                      health?.llm_api_configured ? 'text-emerald-500 bg-emerald-500/10' : 'text-rose-500 bg-rose-500/10'
                    }`}>
                      <div className={`w-2 h-2 rounded-full ${health?.llm_api_configured ? 'bg-emerald-400 animate-pulse' : 'bg-rose-400'}`}></div>
                      {health?.llm_api_configured ? t('Online') : t('Offline')}
                    </div>
                  </div>

                  {/* 7. Unipile Integration */}
                  <div className="p-3 bg-black/40 rounded-lg border border-white/5 flex justify-between items-center gap-3">
                    <div>
                      <div className="font-semibold text-slate-900">{t('Unipile Integration')}</div>
                      <div className="text-xs text-gray-400">{health ? `${health.unipile_accounts} ${t('accounts connected')}` : t('Omnichannel webhooks')}</div>
                    </div>
                    <div className={`flex items-center gap-1.5 text-xs font-semibold px-2.5 py-0.5 rounded-full ${
                      health?.unipile_status === 'online' ? 'text-emerald-500 bg-emerald-500/10' : 'text-rose-500 bg-rose-500/10'
                    }`}>
                      <div className={`w-2 h-2 rounded-full ${health?.unipile_status === 'online' ? 'bg-emerald-400 animate-pulse' : 'bg-rose-400'}`}></div>
                      {health?.unipile_status === 'online' ? t('Online') : t('Offline')}
                    </div>
                  </div>
                </div>
              </div>
              
              <div className="mt-6 pt-6 border-t border-white/10 relative z-10">
                <h3 className="text-sm font-medium text-gray-400 mb-3 flex items-center gap-2">
                  <Zap className="w-4 h-4 text-indigo-500" /> {t('Quick Start')}
                </h3>
                <ul className="space-y-2 text-sm text-gray-300">
                  <li className="flex gap-2"><span className="text-indigo-500">1.</span> {t('Configure emails & personas')}</li>
                  <li className="flex gap-2"><span className="text-indigo-500">2.</span> {t('Create a workflow')}</li>
                  <li className="flex gap-2"><span className="text-indigo-500">3.</span> {t('Let the AI start hunting')}</li>
                </ul>
              </div>
            </motion.div>
          </div>
          </div>
        </>
      )}
    </div>
  );
}
