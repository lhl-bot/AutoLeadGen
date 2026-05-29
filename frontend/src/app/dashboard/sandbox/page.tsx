"use client";

import { useState } from 'react';
import { Search, Loader2, Zap, Target, ArrowRight, Bot, Briefcase } from 'lucide-react';
import { motion } from 'framer-motion';
import { apiFetch, getErrorMessage } from '@/lib/utils';
import type { ResearchResult } from '@/lib/types';
import { useTranslation } from '@/lib/i18n';

export default function SandboxPage() {
  const { t } = useTranslation();
  const [domain, setDomain] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [result, setResult] = useState<ResearchResult | null>(null);
  const [error, setError] = useState('');

  const runResearch = async () => {
    if (!domain) {
      setError(t('Please enter a domain'));
      return;
    }

    setIsLoading(true);
    setError('');
    setResult(null);

    try {
      const cleanDomain = domain.replace('https://', '').replace('http://', '').split('/')[0];
      const response = await apiFetch('/api/leads/research-test', {
        method: 'POST',
        body: JSON.stringify({ domain: cleanDomain })
      });

      if (!response.ok) throw new Error('Research failed');

      const data = await response.json();
      setResult(data);
    } catch (err: unknown) {
      setError(getErrorMessage(err, t('An error occurred during research')));
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div>
      <div className="mb-6">
        <p className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-indigo-600">{t('Assistant')}</p>
        <h1 className="text-2xl font-semibold tracking-tight text-white sm:text-3xl">{t('AI Research Sandbox')}</h1>
        <p className="mt-2 text-sm text-gray-400">{t('Enter a company domain to see what AI research looks like for a lead.')}</p>
      </div>

      <div className="glass-panel rounded-lg p-5 sm:p-6 mb-8">
        <div className="flex flex-col md:flex-row gap-4">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500 w-5 h-5" />
            <input
              type="text"
              placeholder={t('Enter company domain (e.g. vercel.com)')}
              value={domain}
              onChange={(e) => setDomain(e.target.value)}
              className="w-full h-12 bg-black/50 border border-white/10 rounded-lg pl-11 pr-4 text-slate-900 focus:outline-none focus:ring-2 focus:ring-indigo-500/50 transition-all"
              onKeyDown={(e) => e.key === 'Enter' && runResearch()}
            />
          </div>
          <button
            type="button"
            onClick={runResearch}
            disabled={isLoading}
            className="h-12 px-8 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg flex items-center justify-center transition-colors disabled:opacity-50 disabled:cursor-not-allowed font-medium whitespace-nowrap"
          >
            <span className="w-5 h-5 mr-2 flex-shrink-0 flex items-center justify-center">
              {isLoading ? <Loader2 className="w-5 h-5 animate-spin" /> : <Zap className="w-5 h-5" />}
            </span>
            <span>{isLoading ? t('Researching...') : t('Research')}</span>
          </button>
        </div>
        {error && <p className="text-rose-500 text-sm mt-3">{error}</p>}
      </div>

      <div className="min-h-[400px]">
        {isLoading && (
          <div className="flex flex-col items-center justify-center py-20 text-gray-400">
            <div className="relative w-20 h-20 mb-6">
              <div className="absolute inset-0 border-t-2 border-indigo-500 rounded-full animate-spin"></div>
              <div className="absolute inset-2 border-r-2 border-purple-500 rounded-full animate-spin" style={{ animationDirection: 'reverse', animationDuration: '1.5s' }}></div>
              <Bot className="absolute inset-0 m-auto w-8 h-8 text-indigo-500" />
            </div>
            <p className="text-lg animate-pulse font-medium">{t('Researching company website and generating insights...')}</p>
            <p className="text-sm mt-2 text-gray-500">{t('This may take 30-60 seconds')}</p>
          </div>
        )}

        {result && !isLoading && (
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            className="grid gap-6"
          >
            <div className="glass-panel rounded-lg p-6 sm:p-8 relative overflow-hidden">
              <h2 className="text-xl font-bold mb-4 flex items-center gap-2 text-indigo-500">
                <Briefcase className="w-5 h-5" /> {t('Company Overview')}
              </h2>
              <p className="text-gray-300 leading-relaxed text-lg relative z-10">{result.company_overview}</p>
            </div>

            <div className="grid md:grid-cols-2 gap-6">
              <div className="glass-panel rounded-lg p-6 sm:p-8 relative overflow-hidden">
                <h2 className="text-xl font-bold mb-4 flex items-center gap-2 text-orange-500">
                  <Target className="w-5 h-5" /> {t('Pain Points')}
                </h2>
                <p className="text-gray-300 leading-relaxed relative z-10">{result.pain_points}</p>
              </div>

              <div className="glass-panel rounded-lg p-6 sm:p-8 relative overflow-hidden">
                <h2 className="text-xl font-bold mb-4 flex items-center gap-2 text-emerald-500">
                  <ArrowRight className="w-5 h-5" /> {t('Value Proposition Alignment')}
                </h2>
                <p className="text-gray-300 leading-relaxed relative z-10">{result.value_proposition_alignment}</p>
              </div>
            </div>
          </motion.div>
        )}
      </div>
    </div>
  );
}
