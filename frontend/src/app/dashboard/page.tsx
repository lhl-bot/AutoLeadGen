"use client";

import { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { Bot, Mail, Zap, Search, Briefcase, MessageSquare, TrendingUp, Sparkles, ArrowRight } from 'lucide-react';
import { apiFetch } from '@/lib/utils';
import type { DashboardKpis, DashboardTrend, TodayReport } from '@/lib/types';
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

  useEffect(() => {
    const fetchAnalytics = async () => {
      try {
        const res = await apiFetch('/api/analytics/dashboard');
        if (res.ok) {
          const data = await res.json();
          setStats(data.kpis);
          setTrends(data.trends);
          setTodayReport(data.today_report || null);
        }
      } catch (e) {
        console.error("Failed to load analytics", e);
      } finally {
        setIsLoading(false);
      }
    };
    fetchAnalytics();
  }, []);

  return (
    <div>
      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:justify-between sm:items-end">
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-indigo-600">{t('Overview')}</p>
          <h1 className="text-2xl font-semibold tracking-tight text-white sm:text-3xl">{t('Welcome to AutoLeadGen')}</h1>
          <p className="mt-2 text-sm text-gray-400">{t('Your AI-powered outbound sales engine is ready.')}</p>
        </div>
      </div>

      {isLoading ? (
        <div className="py-20 text-center text-gray-500">{t('Loading analytics...')}</div>
      ) : (
        <>
          {/* ── AI Daily Work Report ── */}
          {todayReport && (
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              className="mb-6 p-5 sm:p-6 rounded-xl border border-indigo-500/30 relative overflow-hidden"
              style={{ background: 'linear-gradient(135deg, rgba(79,70,229,0.08) 0%, rgba(16,185,129,0.06) 100%)' }}
            >
              <div className="absolute inset-0 bg-gradient-to-br from-indigo-500/5 via-transparent to-emerald-500/5 pointer-events-none" />
              <div className="relative z-10">
                <h2 className="text-lg font-bold mb-4 flex items-center gap-2 text-white">
                  <Sparkles className="w-5 h-5 text-indigo-400" />
                  {t("Today's AI Work Report")}
                  <span className="text-xs font-normal text-gray-400 ml-2">{t("Today's Report")}</span>
                </h2>
                <div className="grid sm:grid-cols-3 gap-4">
                  <div className="flex items-center gap-4 p-4 rounded-lg bg-black/30 border border-white/5">
                    <div className="p-2.5 bg-emerald-500/10 rounded-lg">
                      <Search className="w-6 h-6 text-emerald-400" />
                    </div>
                    <div>
                      <div className="text-2xl font-bold text-emerald-400">{todayReport.leads_found_today}</div>
                      <div className="text-xs text-gray-400">{t('High-value Leads Found')}</div>
                    </div>
                  </div>
                  <div className="flex items-center gap-4 p-4 rounded-lg bg-black/30 border border-white/5">
                    <div className="p-2.5 bg-indigo-500/10 rounded-lg">
                      <Mail className="w-6 h-6 text-indigo-400" />
                    </div>
                    <div>
                      <div className="text-2xl font-bold text-indigo-400">{todayReport.emails_sent_today}</div>
                      <div className="text-xs text-gray-400">{t('Emails Sent')}</div>
                    </div>
                  </div>
                  <div className="flex items-center gap-4 p-4 rounded-lg bg-black/30 border border-white/5 relative">
                    {todayReport.high_intent_replies > 0 && (
                      <div className="absolute top-2 right-2 w-2.5 h-2.5 rounded-full bg-amber-400 animate-pulse" />
                    )}
                    <div className="p-2.5 bg-amber-500/10 rounded-lg">
                      <MessageSquare className="w-6 h-6 text-amber-400" />
                    </div>
                    <div className="flex-1">
                      <div className="text-2xl font-bold text-amber-400">{todayReport.high_intent_replies}</div>
                      <div className="text-xs text-gray-400">{t('High-intent Replies')}</div>
                    </div>
                    {todayReport.high_intent_replies > 0 && (
                      <a href="/dashboard/replies" className="flex items-center gap-1 text-xs text-amber-400 hover:text-amber-300 transition-colors">
                        {t('Follow up')} <ArrowRight className="w-3.5 h-3.5" />
                      </a>
                    )}
                  </div>
                </div>
                {todayReport.active_workflow_names.length > 0 && (
                  <div className="mt-3 flex items-center gap-2 text-xs text-gray-500">
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
              { label: t("Replies"), value: stats.total_replies, icon: MessageSquare, color: "text-purple-500" }
            ].map((stat, i) => (
              <motion.div 
                key={i}
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.1 }}
                className="glass-panel p-5 rounded-lg border border-white/5 relative overflow-hidden group"
              >
                <div className="flex justify-between items-start mb-5">
                  <div className="p-2.5 bg-white/5 rounded-lg ring-1 ring-black/5">
                    <stat.icon className={`w-6 h-6 ${stat.color}`} />
                  </div>
                </div>
                <div className="text-3xl font-semibold tracking-tight text-slate-900 mb-1">{stat.value}</div>
                <div className="text-sm text-gray-500 font-medium uppercase tracking-wider">{stat.label}</div>
              </motion.div>
            ))}
          </div>

          <div className="grid lg:grid-cols-3 gap-4 mb-8">
            <motion.div 
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ delay: 0.3 }}
              className="glass-panel p-5 sm:p-6 rounded-lg lg:col-span-2 border border-white/5"
            >
              <h2 className="text-lg font-bold mb-6 flex items-center gap-2 text-white">
                <TrendingUp className="w-5 h-5 text-indigo-500" /> {t('Performance Trends (14 Days)')}
              </h2>
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
            </motion.div>

            <motion.div 
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ delay: 0.4 }}
              className="glass-panel p-5 sm:p-6 rounded-lg border border-white/5 relative overflow-hidden flex flex-col justify-between"
            >
              <div>
                <h2 className="text-lg font-bold mb-4 flex items-center gap-2 text-white">
                  <Bot className="w-5 h-5 text-emerald-500" /> {t('System Status')}
                </h2>
                <div className="space-y-4 relative z-10">
                  <div className="p-4 bg-black/40 rounded-lg border border-white/5 flex justify-between items-center gap-4">
                    <div>
                      <div className="font-semibold text-slate-900">{t('Outbound Engine')}</div>
                      <div className="text-xs text-gray-400">{t('Background workers')}</div>
                    </div>
                    <div className="flex items-center gap-2 text-emerald-500 text-xs font-semibold bg-emerald-500/10 px-3 py-1 rounded-full">
                      <div className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></div>
                      {t('Online')}
                    </div>
                  </div>
                  
                  <div className="p-4 bg-black/40 rounded-lg border border-white/5 flex justify-between items-center gap-4">
                    <div>
                      <div className="font-semibold text-slate-900">{t('Research Agent')}</div>
                      <div className="text-xs text-gray-400">{t('DeepSeek LLM Models')}</div>
                    </div>
                    <div className="flex items-center gap-2 text-emerald-500 text-xs font-semibold bg-emerald-500/10 px-3 py-1 rounded-full">
                      <div className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></div>
                      {t('Online')}
                    </div>
                  </div>

                  <div className="p-4 bg-black/40 rounded-lg border border-white/5 flex justify-between items-center gap-4">
                    <div>
                      <div className="font-semibold text-slate-900">{t('Unipile Integration')}</div>
                      <div className="text-xs text-gray-400">{t('Omnichannel webhooks')}</div>
                    </div>
                    <div className="flex items-center gap-2 text-emerald-500 text-xs font-semibold bg-emerald-500/10 px-3 py-1 rounded-full">
                      <div className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></div>
                      {t('Connected')}
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
        </>
      )}
    </div>
  );
}
