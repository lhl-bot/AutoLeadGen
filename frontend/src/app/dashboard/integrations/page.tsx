"use client";

import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from '@/lib/i18n';
import { apiFetch, formatApiDetail } from '@/lib/utils';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Webhook, Plus, Pencil, Trash2, Send, CheckCircle2, XCircle } from 'lucide-react';
import ConfirmDialog from '@/components/ConfirmDialog';
import type { CrmWebhook } from '@/lib/types';

const STAGE_EVENTS = [
  { key: 'lead.contacted', en: 'Contacted', zh: '已联系' },
  { key: 'lead.interested', en: 'Interested', zh: '有意向' },
  { key: 'lead.quoting', en: 'Quoting', zh: '报价中' },
  { key: 'lead.won', en: 'Won', zh: '成交' },
  { key: 'lead.lost', en: 'Lost', zh: '流失' },
  { key: 'lead.replied', en: 'Replied', zh: '已回复' },
];

interface WebhookForm {
  name: string;
  url: string;
  events: string[];
  secret: string;
  is_active: boolean;
}

const emptyForm: WebhookForm = { name: '', url: '', events: ['lead.won'], secret: '', is_active: true };

export default function IntegrationsPage() {
  const { language } = useTranslation();
  const txt = (en: string, zh: string) => (language === 'zh' ? zh : en);
  const [hooks, setHooks] = useState<CrmWebhook[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [form, setForm] = useState<WebhookForm>(emptyForm);
  const [isSaving, setIsSaving] = useState(false);
  const [testingId, setTestingId] = useState<number | null>(null);
  const [deleteId, setDeleteId] = useState<number | null>(null);

  const fetchHooks = useCallback(async () => {
    setIsLoading(true);
    try {
      const res = await apiFetch('/api/crm_webhooks/');
      if (res.ok) setHooks(await res.json());
    } catch (e) {
      console.error(e);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => { fetchHooks(); }, [fetchHooks]);

  const openCreate = () => { setEditingId(null); setForm(emptyForm); setDialogOpen(true); };
  const openEdit = (h: CrmWebhook) => {
    setEditingId(h.id);
    setForm({ name: h.name, url: h.url, events: h.events.split(',').map(s => s.trim()).filter(Boolean), secret: '', is_active: h.is_active });
    setDialogOpen(true);
  };

  const toggleEvent = (key: string) => setForm(f => ({
    ...f,
    events: f.events.includes(key) ? f.events.filter(e => e !== key) : [...f.events, key],
  }));

  const save = async () => {
    if (!form.name.trim() || !form.url.trim()) { toast.error(txt('Name and URL are required.', '名称和 URL 不能为空。')); return; }
    if (!/^https?:\/\//.test(form.url.trim())) { toast.error(txt('URL must start with http:// or https://', 'URL 需以 http:// 或 https:// 开头')); return; }
    if (form.events.length === 0) { toast.error(txt('Select at least one event.', '请至少选择一个事件。')); return; }
    setIsSaving(true);
    try {
      const payload: Record<string, unknown> = {
        name: form.name.trim(), url: form.url.trim(), events: form.events.join(','), is_active: form.is_active,
      };
      if (form.secret.trim()) payload.secret = form.secret.trim();
      const url = editingId ? `/api/crm_webhooks/${editingId}` : '/api/crm_webhooks/';
      const res = await apiFetch(url, {
        method: editingId ? 'PUT' : 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { toast.error(formatApiDetail(data.detail, txt('Save failed.', '保存失败。'))); return; }
      toast.success(editingId ? txt('Webhook updated.', 'Webhook 已更新。') : txt('Webhook created.', 'Webhook 已创建。'));
      setDialogOpen(false);
      await fetchHooks();
    } catch (e) {
      console.error(e);
      toast.error(txt('Network error.', '网络错误。'));
    } finally {
      setIsSaving(false);
    }
  };

  const testHook = async (id: number) => {
    setTestingId(id);
    try {
      const res = await apiFetch(`/api/crm_webhooks/${id}/test`, { method: 'POST' });
      const data = await res.json().catch(() => ({}));
      if (data.ok) toast.success(txt(`Test delivered (HTTP ${data.status}).`, `测试已送达（HTTP ${data.status}）。`));
      else toast.error(txt(`Test failed: ${data.error || data.status}`, `测试失败：${data.error || data.status}`));
      await fetchHooks();
    } catch (e) {
      console.error(e);
      toast.error(txt('Test request failed.', '测试请求失败。'));
    } finally {
      setTestingId(null);
    }
  };

  const doDelete = async () => {
    if (!deleteId) return;
    try {
      const res = await apiFetch(`/api/crm_webhooks/${deleteId}`, { method: 'DELETE' });
      if (res.ok) { toast.success(txt('Webhook deleted.', 'Webhook 已删除。')); await fetchHooks(); }
    } catch (e) {
      console.error(e);
    } finally {
      setDeleteId(null);
    }
  };

  return (
    <div>
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-indigo-600">{txt('Integrations', '集成')}</p>
          <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight text-slate-950 sm:text-3xl">
            <Webhook className="h-7 w-7 text-indigo-500" /> {txt('CRM Webhooks', 'CRM Webhook')}
          </h1>
          <p className="mt-2 max-w-2xl text-sm text-slate-500">
            {txt('Push lead events to your CRM, Zapier, or any HTTP endpoint. Payloads are signed with HMAC-SHA256 when a secret is set.', '将线索事件推送到你的 CRM、Zapier 或任意 HTTP 端点。设置密钥后，推送内容会用 HMAC-SHA256 签名。')}
          </p>
        </div>
        <Button onClick={openCreate}><Plus className="mr-1 h-4 w-4" /> {txt('New Webhook', '新建 Webhook')}</Button>
      </div>

      {isLoading ? (
        <div className="py-20 text-center text-slate-500">{txt('Loading...', '加载中...')}</div>
      ) : hooks.length === 0 ? (
        <div className="rounded-lg border border-dashed border-slate-300 py-20 text-center text-slate-500">
          <Webhook className="mx-auto mb-3 h-8 w-8 text-slate-300" />
          {txt('No webhooks yet. Add one to forward won/interested leads to your CRM.', '还没有 Webhook。添加一个即可把成交/有意向的线索转发到你的 CRM。')}
        </div>
      ) : (
        <div className="space-y-3">
          {hooks.map(h => (
            <div key={h.id} className="glass-panel rounded-lg border border-slate-200 p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-slate-900">{h.name}</span>
                    {h.is_active
                      ? <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[11px] font-medium text-emerald-700">{txt('Active', '启用')}</span>
                      : <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-500">{txt('Paused', '已暂停')}</span>}
                    {h.has_secret && <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-[11px] font-medium text-indigo-600">{txt('Signed', '已签名')}</span>}
                  </div>
                  <div className="mt-1 truncate font-mono text-xs text-slate-500">{h.url}</div>
                  <div className="mt-2 flex flex-wrap gap-1">
                    {h.events.split(',').map(e => e.trim()).filter(Boolean).map(e => (
                      <span key={e} className="rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-600">{e}</span>
                    ))}
                  </div>
                  {h.last_delivered_at && (
                    <div className="mt-2 flex items-center gap-1.5 text-xs">
                      {h.last_error
                        ? <><XCircle className="h-3.5 w-3.5 text-rose-500" /> <span className="text-rose-600">{txt('Last delivery failed', '上次投递失败')}: {h.last_error}</span></>
                        : <><CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" /> <span className="text-slate-500">{txt('Last delivery OK', '上次投递成功')} (HTTP {h.last_status})</span></>}
                    </div>
                  )}
                </div>
                <div className="flex shrink-0 gap-1">
                  <Button size="sm" variant="outline" disabled={testingId === h.id} onClick={() => testHook(h.id)}>
                    <Send className="mr-1 h-3.5 w-3.5" /> {testingId === h.id ? txt('Sending...', '发送中...') : txt('Test', '测试')}
                  </Button>
                  <button onClick={() => openEdit(h)} className="rounded p-2 text-slate-400 hover:bg-slate-100 hover:text-indigo-600" aria-label={txt('Edit', '编辑')}><Pencil className="h-4 w-4" /></button>
                  <button onClick={() => setDeleteId(h.id)} className="rounded p-2 text-slate-400 hover:bg-rose-50 hover:text-rose-600" aria-label={txt('Delete', '删除')}><Trash2 className="h-4 w-4" /></button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{editingId ? txt('Edit Webhook', '编辑 Webhook') : txt('New Webhook', '新建 Webhook')}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-1.5">
              <Label>{txt('Name', '名称')}</Label>
              <Input value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} placeholder={txt('e.g. HubSpot deals', '例如：HubSpot 成交')} />
            </div>
            <div className="space-y-1.5">
              <Label>{txt('Endpoint URL', '端点 URL')}</Label>
              <Input value={form.url} onChange={e => setForm({ ...form, url: e.target.value })} placeholder="https://hooks.zapier.com/..." />
            </div>
            <div className="space-y-1.5">
              <Label>{txt('Signing secret (optional)', '签名密钥（可选）')}</Label>
              <Input type="password" value={form.secret} onChange={e => setForm({ ...form, secret: e.target.value })}
                placeholder={editingId ? txt('Leave blank to keep current', '留空则保持不变') : txt('HMAC-SHA256 secret', 'HMAC-SHA256 密钥')} />
            </div>
            <div className="space-y-1.5">
              <Label>{txt('Trigger on', '触发事件')}</Label>
              <div className="grid grid-cols-2 gap-2">
                {STAGE_EVENTS.map(ev => (
                  <label key={ev.key} className="flex items-center gap-2 rounded-md border border-slate-200 px-2.5 py-1.5 text-sm">
                    <input type="checkbox" checked={form.events.includes(ev.key)} onChange={() => toggleEvent(ev.key)} className="h-4 w-4 accent-indigo-600" />
                    <span className="text-slate-700">{txt(ev.en, ev.zh)}</span>
                    <span className="ml-auto font-mono text-[10px] text-slate-400">{ev.key}</span>
                  </label>
                ))}
              </div>
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={form.is_active} onChange={e => setForm({ ...form, is_active: e.target.checked })} className="h-4 w-4 accent-indigo-600" />
              {txt('Active', '启用')}
            </label>
            <div className="flex justify-end gap-2 pt-1">
              <Button variant="outline" onClick={() => setDialogOpen(false)}>{txt('Cancel', '取消')}</Button>
              <Button onClick={save} disabled={isSaving}>{isSaving ? txt('Saving...', '保存中...') : txt('Save', '保存')}</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={deleteId !== null}
        title={txt('Delete webhook?', '删除 Webhook？')}
        message={txt('Lead events will no longer be forwarded to this endpoint.', '线索事件将不再转发到该端点。')}
        onConfirm={doDelete}
        onCancel={() => setDeleteId(null)}
      />
    </div>
  );
}
