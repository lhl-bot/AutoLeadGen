"use client";

import { useEffect, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Check, ArrowRight, Rocket, X, UserSquare2, Mail, FolderKanban, Workflow, Search, ClipboardCheck } from 'lucide-react';
import { apiFetch } from '@/lib/utils';
import { useTranslation } from '@/lib/i18n';
import type { OnboardingStatus, OnboardingStep } from '@/lib/types';

const DISMISS_KEY = 'onboarding_dismissed';

const STEP_META: Record<OnboardingStep['key'], {
  icon: typeof Mail;
  title: string;
  desc: string;
  href: string;
}> = {
  persona: {
    icon: UserSquare2,
    title: 'Create a customer persona',
    desc: 'Describe your ideal customer so the AI knows who to target.',
    href: '/dashboard/personas',
  },
  email: {
    icon: Mail,
    title: 'Connect a sending mailbox',
    desc: 'Add an SMTP/IMAP account to send and track outreach.',
    href: '/dashboard/emails',
  },
  pool: {
    icon: FolderKanban,
    title: 'Create a client pool',
    desc: 'A workspace to group the leads you source and import.',
    href: '/dashboard/pools',
  },
  workflow: {
    icon: Workflow,
    title: 'Create a workflow (campaign)',
    desc: 'Set up an automated search-to-send campaign.',
    href: '/dashboard/workflows',
  },
  leads: {
    icon: Search,
    title: 'Run your first search',
    desc: 'Let the AI source its first batch of leads.',
    href: '/dashboard/workflows',
  },
  review: {
    icon: ClipboardCheck,
    title: 'Review your first emails',
    desc: 'Approve the AI-drafted emails before they go out.',
    href: '/dashboard/review',
  },
};

export default function OnboardingChecklist() {
  const { t } = useTranslation();
  const [status, setStatus] = useState<OnboardingStatus | null>(null);
  const [dismissed, setDismissed] = useState(true);

  useEffect(() => {
    setDismissed(typeof window !== 'undefined' && localStorage.getItem(DISMISS_KEY) === '1');

    let cancelled = false;
    (async () => {
      try {
        const res = await apiFetch('/api/analytics/onboarding');
        if (res.ok && !cancelled) {
          setStatus(await res.json());
        }
      } catch (e) {
        console.error('Failed to load onboarding status', e);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const dismiss = () => {
    localStorage.setItem(DISMISS_KEY, '1');
    setDismissed(true);
  };

  // Hide once everything is configured, or if the user dismissed it.
  if (!status || dismissed || status.all_done) return null;

  const pct = Math.round((status.completed / status.total) * 100);
  // Surface the next unfinished step first so the user always has a clear next action.
  const firstPendingKey = status.steps.find(s => !s.done)?.key;

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -8 }}
        className="glass-panel mb-6 overflow-hidden rounded-lg border border-indigo-200/70 p-5 sm:p-6"
      >
        <div className="mb-5 flex items-start justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="rounded-lg bg-indigo-500/10 p-2.5">
              <Rocket className="h-5 w-5 text-indigo-500" />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-slate-950">{t('Get started')}</h2>
              <p className="text-sm text-slate-500">{t('Finish setting up your account')}</p>
            </div>
          </div>
          <button
            onClick={dismiss}
            aria-label={t('Dismiss')}
            className="rounded-md p-1.5 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Progress bar */}
        <div className="mb-5">
          <div className="mb-1.5 flex justify-between text-xs font-medium text-slate-500">
            <span>{status.completed}/{status.total} {t('steps done')}</span>
            <span>{pct}%</span>
          </div>
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-100">
            <motion.div
              className="h-full rounded-full bg-indigo-500"
              initial={{ width: 0 }}
              animate={{ width: `${pct}%` }}
              transition={{ duration: 0.5, ease: 'easeOut' }}
            />
          </div>
        </div>

        <ul className="space-y-2">
          {status.steps.map((step, i) => {
            const meta = STEP_META[step.key];
            const Icon = meta.icon;
            const isNext = step.key === firstPendingKey;
            return (
              <motion.li
                key={step.key}
                initial={{ opacity: 0, x: -8 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: i * 0.05 }}
              >
                <a
                  href={meta.href}
                  className={`flex items-center gap-3 rounded-lg border p-3 transition-colors ${
                    step.done
                      ? 'border-emerald-200/70 bg-emerald-50/50'
                      : isNext
                        ? 'border-indigo-300 bg-indigo-50/60 hover:bg-indigo-50'
                        : 'border-slate-200 bg-white hover:bg-slate-50'
                  }`}
                >
                  <div
                    className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full ${
                      step.done ? 'bg-emerald-500 text-white' : 'bg-slate-100 text-slate-500'
                    }`}
                  >
                    {step.done ? <Check className="h-4 w-4" /> : <Icon className="h-4 w-4" />}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className={`text-sm font-medium ${step.done ? 'text-slate-500 line-through' : 'text-slate-900'}`}>
                      {t(meta.title)}
                    </div>
                    {!step.done && (
                      <div className="truncate text-xs text-slate-500">{t(meta.desc)}</div>
                    )}
                  </div>
                  {step.done ? (
                    <span className="shrink-0 text-xs font-medium text-emerald-600">{t('Done')}</span>
                  ) : (
                    <span className={`flex shrink-0 items-center gap-1 text-xs font-medium ${isNext ? 'text-indigo-600' : 'text-slate-400'}`}>
                      {t('Set up')} <ArrowRight className="h-3.5 w-3.5" />
                    </span>
                  )}
                </a>
              </motion.li>
            );
          })}
        </ul>
      </motion.div>
    </AnimatePresence>
  );
}
