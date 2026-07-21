"use client";

import Link from 'next/link';
import {
  ArrowRight,
  BarChart3,
  Clock,
  Globe,
  Mail,
  MessageCircle,
  Search,
  Target,
  Zap,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { useTranslation } from '@/lib/i18n';
import LanguageSwitcher from '@/components/LanguageSwitcher';

export default function LandingPage() {
  const { t } = useTranslation();

  const sequence = [
    { icon: Mail, title: t('Day 1 Email'), detail: t('Day 1 Detail'), color: 'text-sky-300', bg: 'bg-sky-400/10' },
    { icon: Mail, title: t('Day 3 LinkedIn'), detail: t('Day 3 Detail'), color: 'text-blue-300', bg: 'bg-blue-400/10' },
    { icon: MessageCircle, title: t('Day 5 WhatsApp'), detail: t('Day 5 Detail'), color: 'text-emerald-300', bg: 'bg-emerald-400/10' },
  ];

  const features = [
    { icon: Target, title: t('Lead Sourcing'), desc: t('Lead Sourcing Desc') },
    { icon: Globe, title: t('Deep Research'), desc: t('Deep Research Desc') },
    { icon: Mail, title: t('Multichannel'), desc: t('Multichannel Desc') },
    { icon: Zap, title: t('Auto-Drafting'), desc: t('Auto-Drafting Desc') },
    { icon: BarChart3, title: t('Unified Inbox'), desc: t('Unified Inbox Desc') },
    { icon: Clock, title: t('24/7 Execution'), desc: t('24/7 Execution Desc') },
  ];

  return (
    <div className="min-h-screen bg-slate-950 text-white selection:bg-emerald-400/30">
      <nav className="fixed top-0 z-50 w-full border-b border-white/10 bg-slate-950/[0.82] backdrop-blur-md">
        <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-5 sm:px-6">
          <Link href="/" className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-white text-slate-950">
              <Zap className="h-4 w-4" />
            </div>
            <span className="text-lg font-semibold">AutoLeadGen</span>
          </Link>
          <div className="hidden items-center gap-7 text-sm font-medium text-slate-300 md:flex">
            <Link href="#product" className="transition-colors hover:text-white">{t('Product')}</Link>
            <Link href="#solutions" className="transition-colors hover:text-white">{t('Solutions')}</Link>
            <Link href="#pilot" className="transition-colors hover:text-white">邀请制试点</Link>
          </div>
          <div className="flex items-center gap-3">
            <Link href="/login" className="hidden text-sm font-medium text-slate-300 transition-colors hover:text-white sm:inline">
              {t('Log in')}
            </Link>
            <Link href="/login">
              <Button className="h-9 bg-white px-4 text-slate-950 hover:bg-slate-200">
                申请试用
              </Button>
            </Link>
            <LanguageSwitcher />
          </div>
        </div>
      </nav>

      <section className="relative flex min-h-[86dvh] items-center overflow-hidden border-b border-white/10 pt-24">
        <div className="absolute inset-x-0 bottom-0 top-16 opacity-45" aria-hidden="true">
          <div className="mx-auto grid h-full max-w-6xl grid-cols-12 gap-4 px-6">
            <div className="col-span-3 hidden border-x border-white/10 bg-white/[0.03] lg:block" />
            <div className="col-span-12 grid content-center gap-4 lg:col-span-9">
              <div className="rounded-lg border border-white/10 bg-white/[0.06] p-4 shadow-2xl shadow-black/30">
                <div className="mb-4 flex items-center justify-between">
                  <div className="h-3 w-36 rounded bg-white/[0.18]" />
                  <div className="flex gap-2">
                    <div className="h-7 w-20 rounded bg-emerald-400/[0.18]" />
                    <div className="h-7 w-20 rounded bg-sky-400/[0.18]" />
                  </div>
                </div>
                <div className="grid gap-3 sm:grid-cols-3">
                  <div className="rounded-lg border border-white/10 bg-slate-950/70 p-4">
                    <div className="mb-5 h-9 w-9 rounded bg-emerald-400/20" />
                    <div className="mb-2 h-7 w-16 rounded bg-white/[0.18]" />
                    <div className="h-3 w-28 rounded bg-white/[0.12]" />
                  </div>
                  <div className="rounded-lg border border-white/10 bg-slate-950/70 p-4">
                    <div className="mb-5 h-9 w-9 rounded bg-sky-400/20" />
                    <div className="mb-2 h-7 w-20 rounded bg-white/[0.18]" />
                    <div className="h-3 w-24 rounded bg-white/[0.12]" />
                  </div>
                  <div className="rounded-lg border border-white/10 bg-slate-950/70 p-4">
                    <div className="mb-5 h-9 w-9 rounded bg-amber-400/20" />
                    <div className="mb-2 h-7 w-12 rounded bg-white/[0.18]" />
                    <div className="h-3 w-28 rounded bg-white/[0.12]" />
                  </div>
                </div>
              </div>
              <div className="grid gap-4 md:grid-cols-[1.1fr_0.9fr]">
                <div className="rounded-lg border border-white/10 bg-white/[0.05] p-4">
                  <div className="mb-4 h-3 w-32 rounded bg-white/[0.16]" />
                  <div className="flex h-40 items-end gap-2">
                    {[34, 58, 46, 78, 64, 88, 72].map((height, index) => (
                      <div
                        key={index}
                        className="flex-1 rounded-t bg-emerald-300/30"
                        style={{ height: `${height}%` }}
                      />
                    ))}
                  </div>
                </div>
                <div className="rounded-lg border border-white/10 bg-white/[0.05] p-4">
                  <div className="mb-4 h-3 w-28 rounded bg-white/[0.16]" />
                  <div className="space-y-3">
                    {[1, 2, 3, 4].map(item => (
                      <div key={item} className="flex items-center gap-3 rounded border border-white/10 bg-slate-950/[0.55] p-3">
                        <div className="h-8 w-8 rounded bg-white/[0.12]" />
                        <div className="flex-1 space-y-2">
                          <div className="h-2.5 w-3/5 rounded bg-white/[0.18]" />
                          <div className="h-2.5 w-4/5 rounded bg-white/10" />
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div className="relative z-10 mx-auto w-full max-w-7xl px-5 pb-16 text-center sm:px-6">
          <div className="mx-auto max-w-4xl">
            <Badge variant="outline" className="mb-6 border-emerald-300/30 bg-emerald-300/10 px-3 py-1 text-emerald-100">
              <span className="flex items-center gap-2">
                <span className="h-2 w-2 rounded-full bg-emerald-300" />
                {t('Release Scope')}
              </span>
            </Badge>
            <h1 className="text-5xl font-semibold leading-none sm:text-6xl lg:text-7xl">
              AutoLeadGen
            </h1>
            <p className="mx-auto mt-6 max-w-2xl text-base leading-7 text-slate-300 sm:text-lg">
              {t('Hero Desc')}
            </p>
            <div className="mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row">
              <Link href="/login?intent=pilot" className="w-full sm:w-auto">
                <Button size="lg" className="w-full bg-white text-slate-950 hover:bg-slate-200">
                  申请试用 <ArrowRight className="h-4 w-4" />
                </Button>
              </Link>
              <Link href="/login" className="inline-flex min-h-11 w-full items-center justify-center rounded-lg border border-white/20 bg-white/5 px-4 text-sm font-medium text-white hover:bg-white/10 sm:w-auto">登录</Link>
            </div>
          </div>
        </div>
      </section>

      <section id="solutions" className="border-b border-white/10 bg-slate-950 py-20">
        <div className="mx-auto grid max-w-7xl gap-10 px-5 sm:px-6 lg:grid-cols-[0.85fr_1.15fr] lg:items-center">
          <div>
            <p className="mb-3 text-xs font-semibold uppercase text-emerald-300">{t('AI outbound console')}</p>
            <h2 className="text-3xl font-semibold text-white sm:text-4xl">{t('Omnichannel Sequences')}</h2>
            <p className="mt-4 text-base leading-7 text-slate-400">{t('Showcase Desc')}</p>
          </div>
          <div className="grid gap-3">
            {sequence.map((item) => (
              <div
                key={item.title}
                className="rounded-lg border border-white/10 bg-white/[0.05] p-4"
              >
                <div className="flex items-center gap-4">
                  <div className={`flex h-11 w-11 items-center justify-center rounded-lg ${item.bg}`}>
                    <item.icon className={`h-5 w-5 ${item.color}`} />
                  </div>
                  <div>
                    <div className="font-semibold text-white">{item.title}</div>
                    <div className="text-sm text-slate-400">{item.detail}</div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section id="product" className="bg-slate-100 py-20 text-slate-950">
        <div className="mx-auto max-w-7xl px-5 sm:px-6">
          <div className="mb-12 max-w-2xl">
            <p className="mb-3 text-xs font-semibold uppercase text-indigo-600">{t('Product')}</p>
            <h2 className="text-3xl font-semibold sm:text-4xl">{t('Features Title')}</h2>
            <p className="mt-4 text-base leading-7 text-slate-600">{t('Features Desc')}</p>
          </div>

          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {features.map((feature) => (
              <div key={feature.title} className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm transition-colors hover:border-indigo-200">
                <div className="mb-5 flex h-11 w-11 items-center justify-center rounded-lg bg-slate-100 text-indigo-600">
                  <feature.icon className="h-5 w-5" />
                </div>
                <h3 className="text-lg font-semibold">{feature.title}</h3>
                <p className="mt-3 leading-7 text-slate-600">{feature.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section id="pilot" className="bg-white py-20 text-slate-950">
        <div className="mx-auto max-w-4xl px-5 text-center sm:px-6">
          <div className="mx-auto mb-6 flex h-12 w-12 items-center justify-center rounded-lg bg-slate-950 text-white">
            <Search className="h-5 w-5" />
          </div>
          <h2 className="text-3xl font-semibold sm:text-4xl">申请加入邀请制试点</h2>
          <p className="mx-auto mt-4 max-w-2xl text-base leading-7 text-slate-600">试点账号由团队审核开通并预配置发件邮箱；当前不承诺公开注册、免费额度或自助计费。</p>
          <Link href="/login?intent=pilot" className="mt-8 inline-flex">
            <Button size="lg" className="bg-slate-950 text-white hover:bg-slate-800">
              申请试用 <ArrowRight className="h-4 w-4" />
            </Button>
          </Link>
        </div>
      </section>

      <footer className="border-t border-slate-200 bg-white py-10 text-slate-600">
        <div className="mx-auto flex max-w-7xl flex-col items-center justify-between gap-5 px-5 sm:px-6 md:flex-row">
          <div className="flex items-center gap-2 text-slate-950">
            <Zap className="h-5 w-5" />
            <span className="font-semibold">AutoLeadGen</span>
          </div>
          <div className="text-sm">{t('Footer Rights')}</div>
        </div>
      </footer>
    </div>
  );
}
