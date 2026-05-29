"use client";

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import {
  LayoutDashboard,
  Users,
  Mail,
  Settings,
  Menu,
  X,
  MessageSquare,
  Bot,
  Search,
  Database,
  Briefcase,
  History,
  Plus,
  Sparkles,
  PanelLeftClose,
  PanelLeftOpen,
  UserCog,
  LogOut,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn, apiUrl } from '@/lib/utils';
import { useTranslation } from '@/lib/i18n';
import LanguageSwitcher from '@/components/LanguageSwitcher';

const COLLAPSED_STORAGE_KEY = 'autoleadgen.sidebar.collapsed';

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const router = useRouter();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [desktopCollapsed, setDesktopCollapsed] = useState(false);
  const [isAdmin, setIsAdmin] = useState(false);
  const [authChecked, setAuthChecked] = useState(false);
  const { t } = useTranslation();

  // Route guard: check for auth token, redirect to /login if missing.
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const token = window.localStorage.getItem('auth_token');
    if (!token) {
      router.replace('/login');
      return;
    }
    setAuthChecked(true);

    const stored = window.localStorage.getItem(COLLAPSED_STORAGE_KEY);
    if (stored === '1') setDesktopCollapsed(true);

    // Verify admin status from backend, not localStorage
    fetch(apiUrl('/api/auth/me'), {
      headers: { 'Authorization': `Bearer ${token}` },
    }).then(res => {
      if (res.ok) return res.json();
      throw new Error('Failed to fetch user');
    }).then(user => {
      if (user.is_admin) setIsAdmin(true);
      // Sync backend truth to localStorage
      window.localStorage.setItem('auth_user', JSON.stringify(user));
    }).catch(() => {
      // Fall back to localStorage if backend unreachable
      const userStr = window.localStorage.getItem('auth_user');
      if (userStr) {
        try {
          const user = JSON.parse(userStr);
          if (user.is_admin) setIsAdmin(true);
        } catch {}
      }
    });
  }, [router]);

  const handleLogout = () => {
    if (typeof window !== 'undefined') {
      window.localStorage.removeItem('auth_token');
      window.localStorage.removeItem('auth_user');
    }
    router.replace('/login');
  };

  // Show nothing while checking auth
  if (!authChecked) {
    return (
      <div className="min-h-screen bg-slate-950 flex items-center justify-center">
        <div className="w-6 h-6 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  const toggleDesktopCollapsed = () => {
    setDesktopCollapsed(prev => {
      const next = !prev;
      if (typeof window !== 'undefined') {
        window.localStorage.setItem(COLLAPSED_STORAGE_KEY, next ? '1' : '0');
      }
      return next;
    });
  };

  const navigation = [
    { name: t('Overview'), href: '/dashboard', icon: LayoutDashboard, section: 'WORKSPACE' },
    { name: t('Client Pools'), href: '/dashboard/pools', icon: Database, section: 'WORKSPACE' },
    { name: t('Personas'), href: '/dashboard/personas', icon: Users, section: 'WORKSPACE' },
    { name: t('Workflows'), href: '/dashboard/workflows', icon: Briefcase, section: 'WORKSPACE' },
    { name: t('Email Config'), href: '/dashboard/emails', icon: Mail, section: 'WORKSPACE' },
    { name: t('Omnichannel'), href: '/dashboard/settings', icon: Settings, section: 'WORKSPACE' },
    ...(isAdmin ? [{ name: t('Users'), href: '/dashboard/users', icon: UserCog, section: 'WORKSPACE' }] : []),

    { name: t('AI Sandbox'), href: '/dashboard/sandbox', icon: Search, section: 'ASSISTANT' },
    { name: t('AI Agent'), href: '/dashboard/agent', icon: Bot, section: 'ASSISTANT' },

    { name: t('Replies'), href: '/dashboard/replies', icon: MessageSquare, section: 'REPORTS' },
    { name: t('Email Logs'), href: '/dashboard/email-logs', icon: History, section: 'REPORTS' },
  ];

  return (
    <div className="dashboard-ui min-h-screen text-slate-900 flex">
      {/* Mobile sidebar toggle */}
      <div className={cn("lg:hidden fixed top-4 left-4 z-50", sidebarOpen && "hidden")}>
        <Button
          variant="glass"
          size="icon"
          onClick={() => setSidebarOpen(true)}
          aria-label="Open navigation"
          className="border-slate-200 bg-white text-slate-700 shadow-sm hover:bg-slate-50"
        >
          <Menu className="h-5 w-5" />
        </Button>
      </div>

      {sidebarOpen && (
        <button
          type="button"
          aria-label="Close navigation"
          onClick={() => setSidebarOpen(false)}
          className="fixed inset-0 z-30 bg-slate-950/20 backdrop-blur-sm lg:hidden"
        />
      )}

      {/* Sidebar */}
      <div className={cn(
        "fixed inset-y-0 left-0 z-40 w-72 border-r border-slate-200/70 bg-white/90 shadow-xl shadow-slate-900/5 backdrop-blur-xl transform transition-[transform,width] duration-300 ease-in-out lg:translate-x-0 lg:static lg:relative lg:flex lg:flex-col lg:shadow-none",
        sidebarOpen ? "translate-x-0" : "-translate-x-full",
        desktopCollapsed ? "lg:w-[76px]" : "lg:w-72"
      )}>
        <div className={cn(
          "flex h-16 items-center border-b border-slate-200/80 shrink-0 transition-[padding] duration-300",
          desktopCollapsed ? "lg:justify-center lg:px-2 px-5 gap-3" : "px-5 gap-3"
        )}>
          <div className="w-9 h-9 rounded-lg bg-slate-950 flex items-center justify-center shadow-sm shrink-0">
            <Bot className="w-5 h-5 text-white" />
          </div>
          <div className={cn(
            "min-w-0 flex-1 transition-opacity duration-200",
            desktopCollapsed ? "lg:hidden" : "block"
          )}>
            <div className="font-semibold text-base tracking-tight text-slate-950">AutoLeadGen</div>
            <div className="text-xs text-slate-500">{t('AI outbound console')}</div>
          </div>
          
          {/* Mobile close button */}
          <button
            type="button"
            onClick={() => setSidebarOpen(false)}
            aria-label="Close sidebar"
            className="lg:hidden inline-flex h-8 w-8 items-center justify-center rounded-md text-slate-400 hover:text-slate-700 hover:bg-slate-100 transition-colors shrink-0"
          >
            <X className="h-5 w-5" />
          </button>
          
          {/* Desktop collapse button */}
          <button
            type="button"
            onClick={toggleDesktopCollapsed}
            aria-label={desktopCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            title={desktopCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            className={cn(
              "hidden lg:inline-flex h-8 w-8 items-center justify-center rounded-md text-slate-400 hover:text-slate-700 hover:bg-slate-100/80 transition-colors shrink-0",
              desktopCollapsed && "lg:hidden"
            )}
          >
            <PanelLeftClose className="h-4 w-4" />
          </button>
        </div>
 
        {/* Floating expand handle when collapsed */}
        {desktopCollapsed && (
          <button
            type="button"
            onClick={toggleDesktopCollapsed}
            aria-label="Expand sidebar"
            title="Expand sidebar"
            className="hidden lg:flex absolute top-3 -right-3 z-50 h-7 w-7 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-500 hover:text-indigo-600 hover:border-indigo-200 shadow-md transition-colors"
          >
            <PanelLeftOpen className="h-3.5 w-3.5" />
          </button>
        )}
 
        <div className={cn(
          "flex-1 overflow-y-auto flex flex-col gap-5 transition-[padding] duration-300",
          desktopCollapsed ? "lg:py-5 lg:px-2 py-5 px-4 gap-4" : "py-5 px-4"
        )}>
          <Link href="/dashboard/agent" onClick={() => setSidebarOpen(false)} title="New Chat">
            <Button className={cn(
              "w-full bg-slate-950 text-white hover:bg-slate-800",
              desktopCollapsed ? "lg:justify-center lg:px-0 justify-start gap-2" : "justify-start gap-2"
            )}>
              <Plus className="h-4 w-4 shrink-0" />
              <span className={cn(desktopCollapsed ? "lg:hidden" : "inline")}>{t('New Chat')}</span>
            </Button>
          </Link>
 
          {['WORKSPACE', 'ASSISTANT', 'REPORTS'].map((section) => (
            <div key={section} className="flex flex-col gap-1">
              <span className={cn(
                "text-[11px] font-semibold text-slate-400 px-3 mb-2 tracking-[0.14em]",
                desktopCollapsed && "lg:hidden"
              )}>{t(section)}</span>
              {desktopCollapsed && (
                <div className="hidden lg:block border-t border-slate-200/70 mx-2 mb-1" aria-hidden="true" />
              )}
              {navigation.filter(item => item.section === section).map((item) => {
                const isActive = pathname === item.href;
                return (
                  <Link
                    key={item.name}
                    href={item.href}
                    onClick={() => setSidebarOpen(false)}
                    title={desktopCollapsed ? item.name : undefined}
                  >
                    <div className={cn(
                      "flex items-center rounded-lg text-sm font-medium transition-all duration-200",
                      desktopCollapsed
                        ? "lg:justify-center lg:px-0 lg:py-2.5 lg:h-10 lg:w-10 lg:mx-auto gap-3 px-3 py-2"
                        : "gap-3 px-3 py-2",
                      isActive
                        ? "bg-indigo-50 text-indigo-700 shadow-sm ring-1 ring-indigo-100"
                        : "text-slate-600 hover:text-slate-950 hover:bg-slate-100/80"
                    )}>
                      <item.icon className={cn("w-4 h-4 shrink-0", isActive ? "text-indigo-600" : "text-slate-400")} />
                      <span className={cn(desktopCollapsed ? "lg:hidden" : "inline")}>{item.name}</span>
                    </div>
                  </Link>
                );
              })}
            </div>
          ))}
        </div>

        <div className={cn(
          "border-t border-slate-200/80 shrink-0 transition-[padding] duration-300",
          desktopCollapsed ? "lg:px-2 lg:py-3 px-5 py-4" : "px-5 py-4"
        )}>
          {desktopCollapsed ? (
            <div
              className="hidden lg:flex h-9 items-center justify-center rounded-lg border border-emerald-200 bg-emerald-50 text-emerald-600"
              title={`${t('System Online')} — ${t('Workers and API ready')}`}
            >
              <Sparkles className="h-4 w-4" />
            </div>
          ) : null}
          <div className={cn(
            "rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2",
            desktopCollapsed ? "lg:hidden" : "block"
          )}>
            <div className="flex items-center gap-2 text-sm font-medium text-emerald-700">
              <div className="w-2 h-2 rounded-full bg-emerald-500"></div>
              {t('System Online')}
            </div>
            <div className="mt-1 flex items-center gap-1.5 text-xs text-emerald-700/70">
              <Sparkles className="h-3 w-3" />
              {t('Workers and API ready')}
            </div>
          </div>
        </div>
      </div>

      {/* Main content */}
      <div className="flex-1 flex flex-col min-h-screen overflow-hidden">
        {/* Top Header */}
        <header className="h-16 border-b border-slate-200/50 bg-white/50 backdrop-blur-md flex items-center justify-end px-6 lg:px-10 shrink-0">
          {/* Language Switcher & Logout */}
          <div className="flex items-center gap-3">
            <LanguageSwitcher />
            <button
              type="button"
              onClick={handleLogout}
              title={t('Log out')}
              className="inline-flex h-8 w-8 items-center justify-center rounded-md text-slate-400 hover:text-rose-500 hover:bg-rose-50 transition-colors"
            >
              <LogOut className="h-4 w-4" />
            </button>
          </div>
        </header>

        <main className="flex-1 overflow-y-auto px-4 pb-6 pt-6 sm:px-6 lg:px-10 lg:py-8">
          <div className="mx-auto w-full max-w-7xl">
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}
