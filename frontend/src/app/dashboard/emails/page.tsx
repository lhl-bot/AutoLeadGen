"use client";

import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Mail, Plus, RefreshCw, Trash2, CheckCircle2, AlertCircle } from 'lucide-react';
import { apiFetch } from '@/lib/utils';
import { toast } from 'sonner';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { EmailAccount } from '@/lib/types';
import { useTranslation } from '@/lib/i18n';
import ConfirmDialog from '@/components/ConfirmDialog';

interface EmailAccountForm {
  email: string
  display_name: string
  smtp_host: string
  smtp_port: number
  smtp_user: string
  smtp_pass: string
  imap_host: string
  imap_port: number
  use_ssl: boolean
  use_tls: boolean
}

const emptyEmailForm: EmailAccountForm = {
  email: '',
  display_name: '',
  smtp_host: '',
  smtp_port: 465,
  smtp_user: '',
  smtp_pass: '',
  imap_host: '',
  imap_port: 993,
  use_ssl: true,
  use_tls: false
}

export default function EmailsPage() {
  const { t } = useTranslation();
  const [emails, setEmails] = useState<EmailAccount[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  // Form State
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [isCreating, setIsCreating] = useState(false);
  const [formData, setFormData] = useState<EmailAccountForm>(emptyEmailForm);

  // Testing States
  const [testingSmtpId, setTestingSmtpId] = useState<number | null>(null);
  const [testingImapId, setTestingImapId] = useState<number | null>(null);
  const [smtpStatus, setSmtpStatus] = useState<Record<number, { success: boolean; msg: string }>>({});
  const [imapStatus, setImapStatus] = useState<Record<number, { success: boolean; msg: string }>>({});

  // Delete Dialog State
  const [deleteTargetId, setDeleteTargetId] = useState<number | null>(null);
  const [isDeleteOpen, setIsDeleteOpen] = useState(false);

  const testSmtp = async (id: number) => {
    setTestingSmtpId(id);
    try {
      const res = await apiFetch(`/api/email_accounts/${id}/test_smtp`, { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        setSmtpStatus(prev => ({ ...prev, [id]: { success: data.success, msg: data.message } }));
        if (data.success) {
          toast.success(t('SMTP OK'));
        } else {
          toast.error(`${t('SMTP Failed')}: ${data.message}`);
        }
      } else {
        toast.error(t('Failed to contact server for SMTP test.'));
      }
    } catch (e) {
      console.error(e);
      toast.error(t('Network error during SMTP test.'));
    } finally {
      setTestingSmtpId(null);
    }
  };

  const testImap = async (id: number) => {
    setTestingImapId(id);
    try {
      const res = await apiFetch(`/api/email_accounts/${id}/test_imap`, { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        setImapStatus(prev => ({ ...prev, [id]: { success: data.success, msg: data.message } }));
        if (data.success) {
          toast.success(t('IMAP OK'));
        } else {
          toast.error(`${t('IMAP Failed')}: ${data.message}`);
        }
      } else {
        toast.error(t('Failed to contact server for IMAP test.'));
      }
    } catch (e) {
      console.error(e);
      toast.error(t('Network error during IMAP test.'));
    } finally {
      setTestingImapId(null);
    }
  };

  const fetchEmails = async () => {
    setIsLoading(true);
    try {
      const res = await apiFetch('/api/email_accounts/');
      if (res.ok) {
        const data = await res.json();
        setEmails(data);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchEmails();
  }, []);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsCreating(true);
    try {
      // If smtp_user is empty, use the email
      const payload = {
        ...formData,
        smtp_user: formData.smtp_user || formData.email
      };

      const res = await apiFetch('/api/email_accounts/', {
        method: 'POST',
        body: JSON.stringify(payload)
      });
      if (res.ok) {
        setIsCreateOpen(false);
        fetchEmails();
        setFormData(emptyEmailForm);
        toast.success(t('Email account added'));
      } else {
        toast.error(t('Operation failed'));
      }
    } catch (e) {
      console.error(e);
      toast.error(t('Operation failed'));
    } finally {
      setIsCreating(false);
    }
  };

  const openDeleteDialog = (id: number) => {
    setDeleteTargetId(id);
    setIsDeleteOpen(true);
  };

  const handleConfirmDelete = async () => {
    if (deleteTargetId === null) return;
    try {
      await apiFetch(`/api/email_accounts/${deleteTargetId}`, { method: 'DELETE' });
      fetchEmails();
      toast.success(t('Email account deleted'));
    } catch(e) {
      console.error(e);
      toast.error(t('Operation failed'));
    } finally {
      setIsDeleteOpen(false);
      setDeleteTargetId(null);
    }
  };

  const handleCancelDelete = () => {
    setIsDeleteOpen(false);
    setDeleteTargetId(null);
  };

  return (
    <div>
      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:justify-between sm:items-end">
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-indigo-600">{t('Channels')}</p>
          <h1 className="text-2xl font-semibold tracking-tight text-white sm:text-3xl">{t('Sender Emails')}</h1>
          <p className="mt-2 text-sm text-gray-400">{t('Configure SMTP/IMAP accounts for rotating cold emails.')}</p>
        </div>
        <div className="flex flex-wrap gap-3">
          <Button onClick={fetchEmails} variant="outline" className="gap-2 bg-transparent text-slate-700 border-white/20">
            <RefreshCw className="w-4 h-4" /> {t('Refresh')}
          </Button>

          <Dialog open={isCreateOpen} onOpenChange={setIsCreateOpen}>
            <DialogTrigger asChild>
              <Button className="gap-2 bg-indigo-600 hover:bg-indigo-700 text-white border-0">
                <Plus className="w-4 h-4" /> {t('Add Email')}
              </Button>
            </DialogTrigger>
            <DialogContent className="sm:max-w-[500px]">
              <DialogHeader>
                <DialogTitle>{t('Configure Email Account')}</DialogTitle>
              </DialogHeader>
              <form onSubmit={handleCreate} className="space-y-4 mt-4">
                <div className="space-y-2">
                  <Label>{t('Email Address')} *</Label>
                  <Input required type="email" value={formData.email} onChange={e => setFormData({...formData, email: e.target.value, smtp_user: e.target.value})} placeholder="e.g. sales@company.com" />
                </div>
                <div className="space-y-2">
                  <Label>{t('Display Name')}</Label>
                  <Input value={formData.display_name} onChange={e => setFormData({...formData, display_name: e.target.value})} placeholder="e.g. John Doe" />
                </div>

                <div className="grid grid-cols-3 gap-4">
                  <div className="col-span-2 space-y-2">
                    <Label>{t('SMTP Host')} *</Label>
                    <Input required value={formData.smtp_host} onChange={e => setFormData({...formData, smtp_host: e.target.value})} placeholder="smtp.gmail.com" />
                  </div>
                  <div className="space-y-2">
                    <Label>{t('SMTP Port')} *</Label>
                    <Input required type="number" value={formData.smtp_port} onChange={e => setFormData({...formData, smtp_port: parseInt(e.target.value)})} />
                  </div>
                </div>

                <div className="space-y-2">
                  <Label>{t('SMTP Password')} *</Label>
                  <Input required type="password" value={formData.smtp_pass} onChange={e => setFormData({...formData, smtp_pass: e.target.value})} placeholder="••••••••" />
                </div>

                <div className="grid grid-cols-3 gap-4">
                  <div className="col-span-2 space-y-2">
                    <Label>{t('IMAP Host')}</Label>
                    <Input value={formData.imap_host} onChange={e => setFormData({...formData, imap_host: e.target.value})} placeholder="imap.gmail.com" />
                  </div>
                  <div className="space-y-2">
                    <Label>{t('IMAP Port')}</Label>
                    <Input type="number" value={formData.imap_port} onChange={e => setFormData({...formData, imap_port: parseInt(e.target.value)})} />
                  </div>
                </div>

                <div className="flex gap-6 pt-2 pb-2">
                  <label className="flex items-center gap-2 text-sm cursor-pointer">
                    <input type="checkbox" checked={formData.use_ssl} onChange={e => setFormData({...formData, use_ssl: e.target.checked})} className="accent-indigo-500" />
                    {t('Use SSL')}
                  </label>
                  <label className="flex items-center gap-2 text-sm cursor-pointer">
                    <input type="checkbox" checked={formData.use_tls} onChange={e => setFormData({...formData, use_tls: e.target.checked})} className="accent-indigo-500" />
                    {t('Use TLS')}
                  </label>
                </div>

                <div className="pt-4 flex justify-end">
                  <Button type="submit" disabled={isCreating} className="bg-indigo-600 hover:bg-indigo-700 text-white">
                    {isCreating ? t('Saving...') : t('Save Configuration')}
                  </Button>
                </div>
              </form>
            </DialogContent>
          </Dialog>
        </div>
      </div>

      {isLoading ? (
        <div className="py-20 text-center text-gray-500">{t('Loading email accounts...')}</div>
      ) : emails.length === 0 ? (
        <div className="glass-panel p-12 text-center text-gray-400 rounded-lg border border-dashed border-white/20">
          <Mail className="w-12 h-12 mx-auto mb-4 opacity-50" />
          <p>{t('No email accounts added yet.')}</p>
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {emails.map(email => (
            <div key={email.id} className="glass-panel p-5 rounded-lg flex flex-col justify-between hover:border-indigo-500/50 hover:shadow-[0_12px_32px_rgba(79,70,229,0.12)] transition-all">
              <div>
                <div className="flex justify-between items-start mb-2">
                  <div className="flex items-center gap-2">
                    <h3 className="font-bold text-lg text-white">{email.email}</h3>
                    {email.display_name && <span className="text-xs text-gray-400">({email.display_name})</span>}
                  </div>
                  <button onClick={() => openDeleteDialog(email.id)} className="text-gray-500 hover:text-red-400 transition-colors">
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>

                <div className="space-y-2 mt-4 pt-4 border-t border-white/10 text-sm text-gray-300">
                  <div className="flex items-center gap-2">
                    <div className="w-16 text-gray-500">SMTP</div>
                    <div className="flex-1 font-mono text-xs">{email.smtp_host}:{email.smtp_port}</div>
                    {smtpStatus[email.id] ? (
                      smtpStatus[email.id].success ? (
                        <span className="text-xs text-emerald-500 font-semibold">{t('SMTP OK')}</span>
                      ) : (
                        <span className="text-xs text-rose-500 font-semibold cursor-help" title={smtpStatus[email.id].msg}>{t('SMTP Failed')}</span>
                      )
                    ) : (
                      <CheckCircle2 className="w-4 h-4 text-emerald-500" />
                    )}
                    <button
                      type="button"
                      onClick={() => testSmtp(email.id)}
                      disabled={testingSmtpId === email.id}
                      className="text-xs text-indigo-400 hover:text-indigo-300 disabled:opacity-50 transition-colors font-medium ml-2 cursor-pointer"
                    >
                      {testingSmtpId === email.id ? t('Testing...') : t('Test SMTP')}
                    </button>
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="w-16 text-gray-500">IMAP</div>
                    <div className="flex-1 font-mono text-xs">{email.imap_host || t('Not Configured')}:{email.imap_port}</div>
                    {imapStatus[email.id] ? (
                      imapStatus[email.id].success ? (
                        <span className="text-xs text-emerald-500 font-semibold">{t('IMAP OK')}</span>
                      ) : (
                        <span className="text-xs text-rose-500 font-semibold cursor-help" title={imapStatus[email.id].msg}>{t('IMAP Failed')}</span>
                      )
                    ) : email.imap_host ? (
                      <CheckCircle2 className="w-4 h-4 text-emerald-500" />
                    ) : (
                      <AlertCircle className="w-4 h-4 text-amber-500" />
                    )}
                    {email.imap_host && (
                      <button
                        type="button"
                        onClick={() => testImap(email.id)}
                        disabled={testingImapId === email.id}
                        className="text-xs text-indigo-400 hover:text-indigo-300 disabled:opacity-50 transition-colors font-medium ml-2 cursor-pointer"
                      >
                        {testingImapId === email.id ? t('Testing...') : t('Test IMAP')}
                      </button>
                    )}
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      <ConfirmDialog
        open={isDeleteOpen}
        title={t('Confirm Delete')}
        message={t('Are you sure you want to delete this email account?')}
        variant="danger"
        onConfirm={handleConfirmDelete}
        onCancel={handleCancelDelete}
      />
    </div>
  );
}
