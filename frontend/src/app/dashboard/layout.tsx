'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import {
  BarChart3,
  Bot,
  Building2,
  Inbox,
  LayoutDashboard,
  LogOut,
  Menu,
  Shield,
  X,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import NotificationBell from '@/components/NotificationBell';
import LegacyReadOnlySurface from '@/components/LegacyReadOnlySurface';
import { apiFetch, apiUrl, cn } from '@/lib/utils';

const businessNavigation = [
  { name: '工作台', href: '/dashboard', aliases: ['/dashboard/work', '/dashboard/campaigns'], icon: LayoutDashboard },
  { name: '客户', href: '/dashboard/customers', aliases: ['/dashboard/find-customers', '/dashboard/get-started'], icon: Building2 },
  { name: '对话', href: '/dashboard/inbox', icon: Inbox },
  { name: '结果', href: '/dashboard/results', aliases: ['/dashboard/opportunities', '/dashboard/analytics'], icon: BarChart3 },
];

const settingsNavigation = [
  { name: '管理员', href: '/dashboard/admin', icon: Shield },
];

const v2Paths = new Set(['/dashboard', '/dashboard/work', '/dashboard/get-started', '/dashboard/find-customers', '/dashboard/customers', '/dashboard/campaigns', '/dashboard/inbox', '/dashboard/results', '/dashboard/opportunities', '/dashboard/analytics', '/dashboard/admin']);

function isV2Path(pathname: string) {
  return v2Paths.has(pathname) || pathname.startsWith('/dashboard/admin/') || pathname === '/dashboard/settings' || pathname.startsWith('/dashboard/settings/');
}

function readStorageItem(key: string) {
  if (typeof window === 'undefined') return null;
  try { return window.localStorage.getItem(key); } catch { return null; }
}

function removeStorageItem(key: string) {
  if (typeof window === 'undefined') return;
  try { window.localStorage.removeItem(key); } catch { /* restricted browser context */ }
}

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [authChecked, setAuthChecked] = useState(false);
  const [isAdmin, setIsAdmin] = useState(false);

  useEffect(() => {
    const token = readStorageItem('auth_token');
    const cachedUser = readStorageItem('auth_user');
    if (cachedUser) {
      try {
        JSON.parse(cachedUser);
      } catch { /* ignore malformed local cache */ }
    }
    const controller = new AbortController();
    const headers = token ? { Authorization: `Bearer ${token}` } : undefined;
    fetch(apiUrl('/api/auth/me'), { headers, credentials: 'include', signal: controller.signal })
      .then(response => response.ok ? response.json() : Promise.reject(new Error('auth refresh failed')))
      .then((user: { is_admin?: boolean }) => {
        try { window.localStorage.setItem('auth_user', JSON.stringify(user)); } catch { /* restricted context */ }
        setIsAdmin(Boolean(user.is_admin));
        setAuthChecked(true);
      })
      .catch(() => {
        if (controller.signal.aborted) return;
        removeStorageItem('auth_token');
        removeStorageItem('auth_user');
        window.location.replace('/login');
      });
    return () => controller.abort();
  }, []);

  const logout = async () => {
    try {
      await apiFetch('/api/auth/logout', { method: 'POST' });
    } finally {
      removeStorageItem('auth_token');
      removeStorageItem('auth_user');
      router.replace('/login');
    }
  };

  if (!authChecked) {
    return <div className="flex min-h-screen items-center justify-center bg-slate-950"><span className="h-6 w-6 animate-spin rounded-full border-2 border-indigo-400 border-t-transparent motion-reduce:animate-none" aria-label="正在验证登录" /></div>;
  }

  const legacyPage = !isV2Path(pathname);
  const active = (href: string, aliases?: string[]) => pathname === href || Boolean(aliases?.includes(pathname));

  return (
    <div className="dashboard-ui flex min-h-screen bg-slate-50 text-slate-900">
      <a href="#main-content" className="fixed left-3 top-3 z-[70] -translate-y-20 rounded-md bg-slate-950 px-4 py-2 text-sm font-semibold text-white focus:translate-y-0">跳到主要内容</a>
      <Button variant="outline" size="icon" onClick={() => setSidebarOpen(true)} aria-label="打开导航" className={cn('fixed left-4 top-4 z-40 min-h-11 min-w-11 bg-white lg:hidden', sidebarOpen && 'hidden')}><Menu className="h-5 w-5" /></Button>
      {sidebarOpen ? <button type="button" onClick={() => setSidebarOpen(false)} aria-label="关闭导航遮罩" className="fixed inset-0 z-30 bg-slate-950/30 lg:hidden" /> : null}

      <aside className={cn('fixed inset-y-0 left-0 z-40 w-72 -translate-x-full flex-col border-r border-slate-200 bg-white shadow-xl transition-transform lg:sticky lg:top-0 lg:flex lg:h-screen lg:translate-x-0 lg:shadow-none', sidebarOpen ? 'flex translate-x-0' : 'hidden')} aria-label="主导航">
        <div className="flex h-16 items-center gap-3 border-b border-slate-200 px-5">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-slate-950"><Bot className="h-5 w-5 text-white" /></div>
          <div className="min-w-0 flex-1"><p className="font-semibold tracking-tight text-slate-950">AutoLeadGen</p><p className="text-xs text-slate-500">销售助手</p></div>
          <Button variant="ghost" size="icon" onClick={() => setSidebarOpen(false)} aria-label="关闭导航" className="min-h-11 min-w-11 lg:hidden"><X className="h-5 w-5" /></Button>
        </div>
        <nav className="flex-1 overflow-y-auto p-4">
          <p className="px-3 text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">业务工作台</p>
          <ul className="mt-2 space-y-1">
            {businessNavigation.map(item => {
              const selected = active(item.href, item.aliases);
              return <li key={item.href}><Link href={item.href} aria-current={selected ? 'page' : undefined} onClick={() => setSidebarOpen(false)} className={cn('flex min-h-11 items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600', selected ? 'bg-slate-950 text-white' : 'text-slate-700 hover:bg-slate-100 hover:text-slate-950')}><item.icon className={cn('h-4 w-4', selected ? 'text-white' : 'text-slate-500')} />{item.name}</Link></li>;
            })}
          </ul>
          {isAdmin ? <><p className="mt-7 px-3 text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">管理</p>
          <ul className="mt-2 space-y-1">
            {settingsNavigation.map(item => <li key={item.href}><Link href={item.href} aria-current={pathname === item.href ? 'page' : undefined} onClick={() => setSidebarOpen(false)} className={cn('flex min-h-11 items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600', pathname === item.href ? 'bg-slate-200 text-slate-950' : 'text-slate-700 hover:bg-slate-100')}><item.icon className="h-4 w-4 text-slate-500" />{item.name}</Link></li>)}
          </ul>
          </> : null}
        </nav>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex h-16 items-center justify-between border-b border-slate-200 bg-white/95 px-4 backdrop-blur sm:px-6 lg:px-10">
          <div className="ml-14 min-w-0 lg:ml-0"><p className="text-sm font-semibold text-slate-950">销售助手</p><p className="hidden text-xs text-slate-500 sm:block">客户、沟通、结果，一处完成</p></div>
          <div className="flex items-center gap-2"><NotificationBell readOnly /><Button variant="ghost" size="icon" onClick={logout} aria-label="退出登录" className="min-h-11 min-w-11 text-slate-500 hover:text-rose-700"><LogOut className="h-4 w-4" /></Button></div>
        </header>
        <main id="main-content" tabIndex={-1} className="flex-1 px-4 py-6 outline-none sm:px-6 lg:px-10 lg:py-8">
          <div className="mx-auto w-full max-w-7xl">
            {legacyPage ? <div role="status" className="mb-5 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-950"><strong>Legacy 只读页面：</strong>新前端只通过 V2 写入；迁移期保留此页面用于对账，表单与操作按钮已禁用。</div> : null}
            {legacyPage ? <LegacyReadOnlySurface>{children}</LegacyReadOnlySurface> : children}
          </div>
        </main>
      </div>
    </div>
  );
}
