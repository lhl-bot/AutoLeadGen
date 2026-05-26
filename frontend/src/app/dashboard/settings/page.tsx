"use client";

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { Button } from '@/components/ui/button';
import { CheckCircle2, AlertCircle, RefreshCw, Key, Mail, MessageSquare } from 'lucide-react';
import { apiFetch } from '@/lib/utils';
import { toast } from 'sonner';
import type { ChannelAccount } from '@/lib/types';

export default function SettingsPage() {
  const [accounts, setAccounts] = useState<ChannelAccount[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  const fetchAccounts = async () => {
    setIsLoading(true);
    try {
      const res = await apiFetch('/api/channels/accounts');
      if (res.ok) {
        const data = await res.json();
        setAccounts(data);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchAccounts();
  }, []);

  const connectChannel = async (type: string) => {
    try {
      const res = await apiFetch('/api/channels/auth-link', {
        method: 'POST',
        body: JSON.stringify({ type, name: `${type} Account` })
      });
      if (res.ok) {
        const data = await res.json();
        if (data && data.url) {
          window.location.href = data.url;
        } else {
          toast.error('获取授权链接成功，但返回数据中不含 URL。');
        }
      } else {
        const text = await res.text();
        toast.error(`连接发起失败 (状态码 ${res.status}): ${text}`);
      }
    } catch (e) {
      console.error(e);
      toast.error(`网络错误，无法发起连接: ${e}`);
    }
  };

  return (
    <div>
      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:justify-between sm:items-end">
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-indigo-600">Channels</p>
          <h1 className="text-2xl font-semibold tracking-tight text-white sm:text-3xl">Omnichannel Settings</h1>
          <p className="mt-2 text-sm text-gray-400">Manage your connected email, LinkedIn, and WhatsApp accounts for outbound automation.</p>
        </div>
        <Button onClick={fetchAccounts} variant="outline" className="gap-2 bg-transparent text-slate-700 border-white/20">
          <RefreshCw className="w-4 h-4" /> Refresh Status
        </Button>
      </div>

      <div className="grid lg:grid-cols-3 gap-8">
        <div className="lg:col-span-2 space-y-6">
          <div className="glass-panel rounded-lg p-5 sm:p-6">
            <h2 className="text-xl font-bold mb-6 flex items-center gap-2">
              <Key className="w-5 h-5 text-indigo-500" /> Connected Providers
            </h2>
            
            {isLoading ? (
              <div className="py-8 text-center text-gray-500">Loading accounts...</div>
            ) : accounts.length === 0 ? (
              <div className="py-12 text-center text-gray-500 border border-dashed border-white/10 rounded-lg">
                No omnichannel accounts connected yet.
              </div>
            ) : (
              <div className="space-y-4">
                {accounts.map(acc => (
                  <div key={acc.id} className="flex items-center justify-between p-4 bg-white/5 rounded-lg border border-white/10">
                    <div className="flex items-center gap-4">
                      <div className="w-10 h-10 rounded-full bg-white/10 flex items-center justify-center">
                        {acc.account_type === 'LINKEDIN' ? <span className="text-blue-500 font-bold">in</span> : <MessageSquare className="w-5 h-5 text-green-500" />}
                      </div>
                      <div>
                        <div className="font-medium text-slate-900">{acc.name}</div>
                        <div className="text-sm text-gray-400">Type: {acc.account_type}</div>
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      {acc.status === 'OK' ? (
                        <span className="flex items-center gap-1 text-sm text-emerald-500 bg-emerald-400/10 px-2.5 py-1 rounded-full">
                          <CheckCircle2 className="w-4 h-4" /> Connected
                        </span>
                      ) : (
                        <span className="flex items-center gap-1 text-sm text-amber-400 bg-amber-400/10 px-2.5 py-1 rounded-full">
                          <AlertCircle className="w-4 h-4" /> Action Required
                        </span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        <div className="space-y-6">
          <div className="glass-panel rounded-lg p-5 sm:p-6">
            <h2 className="text-xl font-bold mb-4">Add Connections</h2>
            <p className="text-sm text-gray-400 mb-6">Connect your accounts securely via Unipile to enable omnichannel automation.</p>
            
            <div className="space-y-3">
              <Button onClick={() => connectChannel('LINKEDIN')} className="w-full justify-start gap-3 bg-[#0a66c2] hover:bg-[#0a66c2]/90 text-white h-12">
                <span className="font-bold text-lg">in</span> Connect LinkedIn
              </Button>
              <Button onClick={() => connectChannel('WHATSAPP')} className="w-full justify-start gap-3 bg-[#25D366] hover:bg-[#25D366]/90 text-white h-12">
                <MessageSquare className="w-5 h-5" /> Connect WhatsApp
              </Button>
              <Link href="/dashboard/emails" className="block w-full">
                <Button className="w-full justify-start gap-3 bg-white/10 hover:bg-white/20 text-slate-700 h-12 border border-slate-200">
                  <Mail className="w-5 h-5" /> Add Email via SMTP
                </Button>
              </Link>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
