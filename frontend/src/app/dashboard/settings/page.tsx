"use client";

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { Button } from '@/components/ui/button';
import { CheckCircle2, AlertCircle, RefreshCw, Key, Mail, MessageSquare, ExternalLink, Trash2 } from 'lucide-react';
import { apiFetch, isAbortError } from '@/lib/utils';
import { useTranslation } from '@/lib/i18n';
import { toast } from 'sonner';
import type { ChannelAccount } from '@/lib/types';
import ConfirmDialog from '@/components/ConfirmDialog';

type ChannelProvider = 'LINKEDIN' | 'WHATSAPP';

export default function SettingsPage() {
  const { t } = useTranslation();
  const [accounts, setAccounts] = useState<ChannelAccount[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isSyncing, setIsSyncing] = useState(false);
  const [fetchError, setFetchError] = useState(false);
  const [connectingKey, setConnectingKey] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ChannelAccount | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);

  const fetchAccounts = async ({ sync = false }: { sync?: boolean } = {}) => {
    setIsLoading(true);
    setIsSyncing(sync);
    setFetchError(false);
    let timeoutId: ReturnType<typeof setTimeout> | undefined;
    try {
      const controller = new AbortController();
      timeoutId = setTimeout(() => {
        controller.abort(new DOMException('Channel account sync timed out', 'TimeoutError'));
      }, 10000);
      const res = await apiFetch(`/api/channels/accounts${sync ? '?sync=true' : ''}`, { signal: controller.signal });
      if (timeoutId) {
        clearTimeout(timeoutId);
        timeoutId = undefined;
      }
      if (res.ok) {
        const data = await res.json();
        setAccounts(data);
      } else {
        setFetchError(true);
      }
    } catch (e) {
      if (!isAbortError(e)) {
        console.error(e);
      }
      setFetchError(true);
    } finally {
      if (timeoutId) {
        clearTimeout(timeoutId);
      }
      setIsLoading(false);
      setIsSyncing(false);
    }
  };

  useEffect(() => {
    fetchAccounts();
  }, []);

  const normaliseProvider = (type: string): ChannelProvider | null => {
    const upper = type.toUpperCase();
    if (upper === 'LINKEDIN' || upper === 'WHATSAPP') {
      return upper;
    }
    return null;
  };

  const connectChannel = async (type: string, name?: string | null, key = type) => {
    const provider = normaliseProvider(type);
    if (!provider) {
      toast.error(`${t('Connection failed')}: ${type}`);
      return;
    }
    setConnectingKey(key);
    try {
      const res = await apiFetch('/api/channels/auth-link', {
        method: 'POST',
        body: JSON.stringify({ type: provider, name: name || `${provider} Account` })
      });
      if (res.ok) {
        const data = await res.json();
        if (data && data.url) {
          toast.success(t('Connection initiated'));
          window.location.assign(data.url);
        } else {
          toast.error(t('Connection failed'));
        }
      } else {
        const text = await res.text();
        toast.error(`${t('Connection failed')} (${res.status}): ${text}`);
      }
    } catch (e) {
      console.error(e);
      const message = e instanceof Error ? e.message : String(e);
      toast.error(`${t('Network error')}: ${message}`);
    } finally {
      setConnectingKey(null);
    }
  };

  const deleteChannelAccount = async () => {
    if (!deleteTarget) return;
    setIsDeleting(true);
    try {
      const res = await apiFetch(`/api/channels/accounts/${deleteTarget.id}`, {
        method: 'DELETE',
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || `HTTP ${res.status}`);
      }
      setAccounts(prev => prev.filter(account => account.id !== deleteTarget.id));
      toast.success(t('Channel account deleted'));
      setDeleteTarget(null);
    } catch (e) {
      console.error(e);
      const message = e instanceof Error ? e.message : String(e);
      toast.error(`${t('Operation failed')}: ${message}`);
    } finally {
      setIsDeleting(false);
    }
  };

  return (
    <div>
      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:justify-between sm:items-end">
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-indigo-600">{t('Channels')}</p>
          <h1 className="text-2xl font-semibold tracking-tight text-white sm:text-3xl">{t('Omnichannel Settings')}</h1>
          <p className="mt-2 text-sm text-slate-500">{t('Connect your LinkedIn and WhatsApp accounts to enable multi-channel outreach.')}</p>
        </div>
        <Button onClick={() => fetchAccounts({ sync: true })} disabled={isSyncing} variant="outline" className="gap-2 bg-transparent text-slate-700 border-slate-300">
          <RefreshCw className={`w-4 h-4 ${isSyncing ? 'animate-spin' : ''}`} /> {isSyncing ? t('Syncing...') : t('Sync status')}
        </Button>
      </div>

      <div className="grid lg:grid-cols-3 gap-8">
        <div className="lg:col-span-2 space-y-6">
          <div className="glass-panel rounded-lg p-5 sm:p-6">
            <h2 className="text-xl font-bold mb-6 flex items-center gap-2">
              <Key className="w-5 h-5 text-indigo-500" /> {t('Connected Providers')}
            </h2>

            {isLoading ? (
              <div className="py-8 text-center text-slate-500">{t('Loading channel accounts...')}</div>
            ) : fetchError ? (
              <div className="py-12 text-center text-slate-500 border border-dashed border-red-500/20 rounded-lg">
                <p>{t('Network error')}</p>
                <button onClick={() => fetchAccounts()} className="mt-3 text-sm text-indigo-400 hover:text-indigo-300 underline">{t('Refresh')}</button>
              </div>
            ) : accounts.length === 0 ? (
              <div className="py-12 text-center text-slate-500 border border-dashed border-slate-200 rounded-lg">
                {t('No omnichannel accounts connected yet.')}
              </div>
            ) : (
              <div className="space-y-4">
                {accounts.map(acc => {
                  const provider = normaliseProvider(acc.account_type);
                  const reconnectKey = `account-${acc.id}`;
                  const isConnecting = connectingKey === reconnectKey;

                  return (
                  <div key={acc.id} className="flex flex-col gap-4 p-4 bg-slate-50 rounded-lg border border-slate-200 sm:flex-row sm:items-center sm:justify-between">
                    <div className="flex min-w-0 items-center gap-4">
                      <div className="w-10 h-10 rounded-full bg-slate-100 flex items-center justify-center">
                        {acc.account_type === 'LINKEDIN' ? <span className="text-blue-500 font-bold">in</span> : <MessageSquare className="w-5 h-5 text-green-500" />}
                      </div>
                      <div className="min-w-0">
                        <div className="truncate font-medium text-white">{acc.name || t('Unnamed account')}</div>
                        <div className="text-sm text-slate-500">{t('Type')}: {acc.account_type}</div>
                        <div className="text-xs text-slate-500">{t('Status')}: {acc.status}</div>
                      </div>
                    </div>
                    <div className="flex flex-wrap items-center gap-2 sm:justify-end">
                      {acc.status === 'OK' ? (
                        <span className="flex items-center gap-1 text-sm text-emerald-500 bg-emerald-400/10 px-2.5 py-1 rounded-full">
                          <CheckCircle2 className="w-4 h-4" /> {t('Connected')}
                        </span>
                      ) : (
                        <>
                          <span className="flex items-center gap-1 rounded-full bg-amber-50 px-2.5 py-1 text-sm text-amber-700 ring-1 ring-amber-200">
                            <AlertCircle className="w-4 h-4" /> {t('Action Required')}
                          </span>
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={!provider || isConnecting}
                            onClick={() => connectChannel(acc.account_type, acc.name, reconnectKey)}
                            className="gap-2 border-amber-300 bg-amber-50 text-amber-800 hover:bg-amber-100 hover:text-amber-900"
                          >
                            {isConnecting ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <ExternalLink className="w-3.5 h-3.5" />}
                            {isConnecting ? t('Reconnecting...') : t('Reconnect')}
                          </Button>
                        </>
                      )}
                      <Button
                        size="icon-sm"
                        variant="ghost"
                        onClick={() => setDeleteTarget(acc)}
                        className="text-slate-400 hover:bg-rose-50 hover:text-rose-600"
                        aria-label={t('Delete channel account')}
                        title={t('Delete channel account')}
                      >
                        <Trash2 className="w-4 h-4" />
                      </Button>
                    </div>
                  </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>

        <div className="space-y-6">
          <div className="glass-panel rounded-lg p-5 sm:p-6">
            <h2 className="text-xl font-bold mb-4">{t('Add Connections')}</h2>
            <p className="text-sm text-slate-500 mb-6">{t('Connect your accounts securely via Unipile to enable omnichannel automation.')}</p>

            <div className="space-y-3">
              <Button onClick={() => connectChannel('LINKEDIN')} className="w-full justify-start gap-3 bg-[#0a66c2] hover:bg-[#0a66c2]/90 text-white h-12">
                <span className="font-bold text-lg">in</span> {t('Connect LinkedIn')}
              </Button>
              <Button onClick={() => connectChannel('WHATSAPP')} className="w-full justify-start gap-3 bg-[#25D366] hover:bg-[#25D366]/90 text-white h-12">
                <MessageSquare className="w-5 h-5" /> {t('Connect WhatsApp')}
              </Button>
              <Link href="/dashboard/emails" className="block w-full">
                <Button className="w-full justify-start gap-3 bg-slate-100 hover:bg-slate-200 text-slate-700 h-12 border border-slate-200">
                  <Mail className="w-5 h-5" /> {t('Add Email via SMTP')}
                </Button>
              </Link>
            </div>
          </div>
        </div>
      </div>

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        title={t('Delete channel account')}
        message={t('Are you sure you want to delete this channel account from AutoLeadGen?')}
        confirmLabel={isDeleting ? t('Deleting...') : t('Yes, delete')}
        onConfirm={deleteChannelAccount}
        onCancel={() => {
          if (!isDeleting) {
            setDeleteTarget(null);
          }
        }}
      />
    </div>
  );
}
