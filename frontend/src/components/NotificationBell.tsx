"use client";

import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Bell, MailCheck, AlertTriangle, WalletCards, CheckCheck } from 'lucide-react';
import { apiFetch } from '@/lib/utils';
import { useTranslation } from '@/lib/i18n';

interface NotificationItem {
  id: number;
  type: string;
  title: string;
  body?: string | null;
  link?: string | null;
  is_read: boolean;
  created_at: string;
}

const POLL_MS = 60000;

function iconFor(type: string) {
  if (type === 'high_intent_reply') return MailCheck;
  if (type === 'low_balance') return WalletCards;
  return AlertTriangle;
}

function colorFor(type: string) {
  if (type === 'high_intent_reply') return 'text-emerald-500';
  if (type === 'low_balance') return 'text-amber-500';
  return 'text-rose-500';
}

function timeAgo(iso: string, zh: boolean): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '';
  const mins = Math.floor((Date.now() - then) / 60000);
  if (mins < 1) return zh ? '刚刚' : 'just now';
  if (mins < 60) return zh ? `${mins} 分钟前` : `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return zh ? `${hrs} 小时前` : `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return zh ? `${days} 天前` : `${days}d ago`;
}

export default function NotificationBell() {
  const { language } = useTranslation();
  const zh = language === 'zh';
  const txt = (en: string, z: string) => (zh ? z : en);
  const router = useRouter();
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const fetchNotifications = useCallback(async () => {
    try {
      const res = await apiFetch('/api/notifications?limit=20');
      if (res.ok) {
        const data = await res.json();
        setItems(data.items || []);
        setUnread(data.unread_count || 0);
      }
    } catch (e) {
      console.error(e);
    }
  }, []);

  useEffect(() => {
    fetchNotifications();
    const timer = window.setInterval(fetchNotifications, POLL_MS);
    return () => window.clearInterval(timer);
  }, [fetchNotifications]);

  // Close on outside click.
  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, []);

  const markAllRead = async () => {
    setUnread(0);
    setItems(prev => prev.map(i => ({ ...i, is_read: true })));
    try { await apiFetch('/api/notifications/read-all', { method: 'POST' }); } catch (e) { console.error(e); }
  };

  const openItem = async (item: NotificationItem) => {
    if (!item.is_read) {
      setItems(prev => prev.map(i => (i.id === item.id ? { ...i, is_read: true } : i)));
      setUnread(u => Math.max(0, u - 1));
      try { await apiFetch(`/api/notifications/${item.id}/read`, { method: 'POST' }); } catch (e) { console.error(e); }
    }
    setOpen(false);
    if (item.link) router.push(item.link);
  };

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        title={txt('Notifications', '通知')}
        className="relative inline-flex h-8 w-8 items-center justify-center rounded-md text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600"
      >
        <Bell className="h-4 w-4" />
        {unread > 0 && (
          <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-rose-500 px-1 text-[10px] font-bold leading-none text-white">
            {unread > 9 ? '9+' : unread}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 z-50 mt-2 w-80 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-xl">
          <div className="flex items-center justify-between border-b border-slate-100 px-4 py-2.5">
            <span className="text-sm font-semibold text-slate-800">{txt('Notifications', '通知')}</span>
            {unread > 0 && (
              <button onClick={markAllRead} className="flex items-center gap-1 text-xs text-indigo-600 hover:text-indigo-700">
                <CheckCheck className="h-3.5 w-3.5" /> {txt('Mark all read', '全部已读')}
              </button>
            )}
          </div>
          <div className="max-h-96 overflow-y-auto">
            {items.length === 0 ? (
              <div className="py-10 text-center text-sm text-slate-400">{txt('No notifications', '暂无通知')}</div>
            ) : (
              items.map(item => {
                const Icon = iconFor(item.type);
                return (
                  <button
                    key={item.id}
                    onClick={() => openItem(item)}
                    className={`flex w-full gap-3 border-b border-slate-50 px-4 py-3 text-left transition-colors hover:bg-slate-50 ${item.is_read ? '' : 'bg-indigo-50/40'}`}
                  >
                    <Icon className={`mt-0.5 h-4 w-4 shrink-0 ${colorFor(item.type)}`} />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-start justify-between gap-2">
                        <span className={`text-sm ${item.is_read ? 'font-normal text-slate-600' : 'font-medium text-slate-900'}`}>{item.title}</span>
                        {!item.is_read && <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-indigo-500" />}
                      </div>
                      {item.body && <p className="mt-0.5 line-clamp-2 text-xs text-slate-500">{item.body}</p>}
                      <span className="mt-1 block text-[11px] text-slate-400">{timeAgo(item.created_at, zh)}</span>
                    </div>
                  </button>
                );
              })
            )}
          </div>
        </div>
      )}
    </div>
  );
}
