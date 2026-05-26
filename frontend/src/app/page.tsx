"use client";

import { motion } from 'framer-motion';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Mail, MessageCircle, ArrowRight, CheckCircle2, Play, Users, Target, Zap, BarChart3, Clock, Globe } from 'lucide-react';
import Link from 'next/link';
import { useTranslation } from '@/lib/i18n';
import LanguageSwitcher from '@/components/LanguageSwitcher';

export default function LandingPage() {
  const { t } = useTranslation();

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-foreground font-sans selection:bg-indigo-500/30">
      {/* Navbar */}
      <nav className="fixed top-0 w-full z-50 bg-[#0a0a0a]/80 backdrop-blur-md border-b border-white/10">
        <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 bg-indigo-600 rounded-lg flex items-center justify-center">
              <Zap className="w-5 h-5 text-white" />
            </div>
            <span className="text-xl font-bold tracking-tight text-white">AutoLeadGen</span>
          </div>
          <div className="hidden md:flex items-center gap-8 text-sm font-medium text-gray-400">
            <Link href="#product" className="hover:text-white transition-colors">{t('Product')}</Link>
            <Link href="#solutions" className="hover:text-white transition-colors">{t('Solutions')}</Link>
            <Link href="#pricing" className="hover:text-white transition-colors">{t('Pricing')}</Link>
          </div>
          <div className="flex items-center gap-4">
            <Link href="/login" className="text-sm font-medium text-gray-300 hover:text-white transition-colors">
              {t('Log in')}
            </Link>
            <Link href="/login">
              <Button className="bg-white text-black hover:bg-gray-200 rounded-full px-6">
                {t('Get Started')}
              </Button>
            </Link>

            <LanguageSwitcher />
          </div>
        </div>
      </nav>

      {/* Hero Section */}
      <section className="relative pt-40 pb-20 overflow-hidden">
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-indigo-900/40 via-[#0a0a0a] to-[#0a0a0a] -z-10" />
        
        <div className="max-w-7xl mx-auto px-6">
          <div className="text-center max-w-4xl mx-auto">
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.5 }}
            >
                <Badge variant="outline" className="mb-6 border-indigo-500/30 text-indigo-300 bg-indigo-500/10 px-4 py-1.5 rounded-full">
                  <span className="flex items-center gap-2">
                    <span className="relative flex h-2 w-2">
                      <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-indigo-400 opacity-75"></span>
                      <span className="relative inline-flex rounded-full h-2 w-2 bg-indigo-500"></span>
                    </span>
                    AutoLeadGen 2.0 is live
                  </span>
                </Badge>
                <h1 className="text-6xl md:text-7xl font-extrabold tracking-tighter text-white mb-8 leading-[1.1]">
                  {t('Hero Title Line 1')} <br className="hidden md:block" />
                  <span className="text-transparent bg-clip-text bg-gradient-to-r from-indigo-400 via-purple-400 to-pink-400">
                    {t('Hero Title Line 2')}
                  </span>
                </h1>
                <p className="text-xl text-gray-400 mb-10 max-w-2xl mx-auto font-light leading-relaxed">
                  {t('Hero Desc')}
                </p>
                <div className="flex flex-col sm:flex-row items-center justify-center gap-4">
                  <Link href="/login" className="w-full sm:w-auto">
                    <Button size="lg" className="bg-white text-black hover:bg-gray-200 rounded-full h-14 px-8 text-lg w-full">
                      {t('Free Trial')} <ArrowRight className="ml-2 w-5 h-5" />
                    </Button>
                  </Link>
                  <Button size="lg" variant="outline" className="rounded-full h-14 px-8 text-lg border-white/20 text-white hover:bg-white/5 w-full sm:w-auto">
                    <Play className="mr-2 w-5 h-5" /> {t('Watch Demo')}
                  </Button>
                </div>
                <div className="mt-10 flex items-center justify-center gap-6 text-sm text-gray-500">
                  <span className="flex items-center gap-2"><CheckCircle2 className="w-4 h-4 text-emerald-500" /> {t('No CC')}</span>
                  <span className="flex items-center gap-2"><CheckCircle2 className="w-4 h-4 text-emerald-500" /> {t('14 Days')}</span>
                </div>
            </motion.div>
          </div>

          {/* Omnichannel Animation Showcase */}
          <motion.div 
            initial={{ opacity: 0, y: 40 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.7, delay: 0.2 }}
            className="mt-24 relative"
          >
              <div className="absolute -inset-1 bg-gradient-to-r from-indigo-500 via-purple-500 to-pink-500 rounded-[2.5rem] blur opacity-20" />
              <div className="relative bg-[#111] border border-white/10 rounded-[2rem] p-8 md:p-12 shadow-2xl overflow-hidden">
                <div className="absolute top-0 right-0 w-96 h-96 bg-indigo-500/10 blur-[100px] rounded-full pointer-events-none" />
                <div className="absolute bottom-0 left-0 w-96 h-96 bg-purple-500/10 blur-[100px] rounded-full pointer-events-none" />
                
                <div className="flex flex-col md:flex-row items-center justify-between gap-12 relative z-10">
                  <div className="flex-1 space-y-6">
                    <h3 className="text-2xl md:text-3xl font-bold text-white">{t('Omnichannel Sequences')}</h3>
                    <p className="text-gray-400 text-lg">{t('Showcase Desc')}</p>
                  </div>
                  
                  <div className="flex-1 w-full max-w-md relative">
                    <div className="absolute left-6 top-6 bottom-6 w-0.5 bg-white/10" />
                    
                    <motion.div 
                      initial={{ x: 50, opacity: 0 }} animate={{ x: 0, opacity: 1 }} transition={{ delay: 0.5 }}
                      className="relative pl-16 py-4"
                    >
                      <div className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 rounded-full bg-[#111] border-2 border-indigo-500 z-10" />
                      <Card className="bg-black/50 border-white/10 backdrop-blur-md">
                        <CardContent className="p-4 flex items-center gap-4">
                          <div className="w-10 h-10 rounded-full bg-blue-500/20 flex items-center justify-center">
                            <Mail className="w-5 h-5 text-blue-400" />
                          </div>
                          <div>
                            <div className="text-sm font-semibold text-white">{t('Day 1 Email')}</div>
                            <div className="text-xs text-gray-500">{t('Day 1 Detail')}</div>
                          </div>
                        </CardContent>
                      </Card>
                    </motion.div>

                    <motion.div 
                      initial={{ x: 50, opacity: 0 }} animate={{ x: 0, opacity: 1 }} transition={{ delay: 0.9 }}
                      className="relative pl-16 py-4"
                    >
                      <div className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 rounded-full bg-[#111] border-2 border-[#0a66c2] z-10" />
                      <Card className="bg-black/50 border-white/10 backdrop-blur-md">
                        <CardContent className="p-4 flex items-center gap-4">
                          <div className="w-10 h-10 rounded-full bg-[#0a66c2]/20 flex items-center justify-center">
                            <span className="text-[#0a66c2] font-bold">in</span>
                          </div>
                          <div>
                            <div className="text-sm font-semibold text-white">{t('Day 3 LinkedIn')}</div>
                            <div className="text-xs text-gray-500">{t('Day 3 Detail')}</div>
                          </div>
                        </CardContent>
                      </Card>
                    </motion.div>

                    <motion.div 
                      initial={{ x: 50, opacity: 0 }} animate={{ x: 0, opacity: 1 }} transition={{ delay: 1.3 }}
                      className="relative pl-16 py-4"
                    >
                      <div className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 rounded-full bg-[#111] border-2 border-[#25D366] z-10" />
                      <Card className="bg-black/50 border-white/10 backdrop-blur-md">
                        <CardContent className="p-4 flex items-center gap-4">
                          <div className="w-10 h-10 rounded-full bg-[#25D366]/20 flex items-center justify-center">
                            <MessageCircle className="w-5 h-5 text-[#25D366]" />
                          </div>
                          <div>
                            <div className="text-sm font-semibold text-white">{t('Day 5 WhatsApp')}</div>
                            <div className="text-xs text-gray-500">{t('Day 5 Detail')}</div>
                          </div>
                        </CardContent>
                      </Card>
                    </motion.div>
                  </div>
                </div>
              </div>
          </motion.div>
        </div>
      </section>

      {/* Features Grid */}
      <section id="product" className="py-24 bg-black">
        <div className="max-w-7xl mx-auto px-6">
          <div className="text-center mb-16">
            <h2 className="text-4xl font-bold text-white mb-4">{t('Features Title')}</h2>
            <p className="text-xl text-gray-400">{t('Features Desc')}</p>
          </div>
          
          <div className="grid md:grid-cols-3 gap-8">
            {[
              { icon: Target, title: t('Lead Sourcing'), desc: t('Lead Sourcing Desc') },
              { icon: Globe, title: t('Deep Research'), desc: t('Deep Research Desc') },
              { icon: Users, title: t('Multichannel'), desc: t('Multichannel Desc') },
              { icon: Zap, title: t('Auto-Drafting'), desc: t('Auto-Drafting Desc') },
              { icon: BarChart3, title: t('Unified Inbox'), desc: t('Unified Inbox Desc') },
              { icon: Clock, title: t('24/7 Execution'), desc: t('24/7 Execution Desc') }
            ].map((f, i) => (
              <div key={i} className="bg-[#111] border border-white/5 p-8 rounded-2xl hover:border-indigo-500/30 transition-colors">
                <div className="w-12 h-12 rounded-xl bg-white/5 flex items-center justify-center mb-6">
                  <f.icon className="w-6 h-6 text-indigo-400" />
                </div>
                <h3 className="text-xl font-bold text-white mb-3">{f.title}</h3>
                <p className="text-gray-400 leading-relaxed">{f.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* CTA Section */}
      <section className="py-32 relative overflow-hidden">
        <div className="absolute inset-0 bg-indigo-600/10" />
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[800px] h-[800px] bg-indigo-600/20 rounded-full blur-[120px] pointer-events-none" />
        
        <div className="max-w-4xl mx-auto px-6 text-center relative z-10">
          <h2 className="text-5xl font-bold text-white mb-8">{t('CTA Title')}</h2>
          <p className="text-xl text-gray-300 mb-10">{t('CTA Desc')}</p>
          <Link href="/login">
            <Button size="lg" className="bg-white text-black hover:bg-gray-200 rounded-full h-14 px-10 text-lg font-bold">
              {t('CTA Button')}
            </Button>
          </Link>
        </div>
      </section>

      {/* Footer */}
      <footer className="border-t border-white/10 bg-[#0a0a0a] py-12">
        <div className="max-w-7xl mx-auto px-6 flex flex-col md:flex-row justify-between items-center gap-6">
          <div className="flex items-center gap-2">
            <Zap className="w-5 h-5 text-indigo-500" />
            <span className="text-lg font-bold text-white">AutoLeadGen</span>
          </div>
          <div className="text-gray-500 text-sm">
            {t('Footer Rights')}
          </div>
        </div>
      </footer>
    </div>
  );
}
