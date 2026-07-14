"use client";

import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from '@/lib/i18n';
import { apiFetch, formatApiDetail } from '@/lib/utils';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { FileText, Plus, Pencil, Trash2, FlaskConical, Eye } from 'lucide-react';
import ConfirmDialog from '@/components/ConfirmDialog';
import type { EmailTemplate } from '@/lib/types';

const VARIABLES = ['first_name', 'last_name', 'full_name', 'company_name', 'job_title', 'domain'];

interface TemplateForm {
  name: string;
  category: string;
  ab_group: string;
  variant_label: string;
  subject: string;
  body: string;
  weight: number;
  is_active: boolean;
}

const emptyForm: TemplateForm = {
  name: '', category: 'cold', ab_group: '', variant_label: 'A',
  subject: '', body: '', weight: 1, is_active: true,
};

export default function TemplatesPage() {
  const { language } = useTranslation();
  const txt = (en: string, zh: string) => (language === 'zh' ? zh : en);
  const [templates, setTemplates] = useState<EmailTemplate[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [form, setForm] = useState<TemplateForm>(emptyForm);
  const [isSaving, setIsSaving] = useState(false);
  const [preview, setPreview] = useState<{ subject: string; body: string; unknown_variables: string[] } | null>(null);
  const [deleteId, setDeleteId] = useState<number | null>(null);

  const fetchTemplates = useCallback(async () => {
    setIsLoading(true);
    try {
      const res = await apiFetch('/api/email_templates/');
      if (res.ok) setTemplates(await res.json());
    } catch (e) {
      console.error(e);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => { fetchTemplates(); }, [fetchTemplates]);

  const openCreate = () => { setEditingId(null); setForm(emptyForm); setPreview(null); setDialogOpen(true); };
  const openEdit = (t: EmailTemplate) => {
    setEditingId(t.id);
    setForm({
      name: t.name, category: t.category, ab_group: t.ab_group || '',
      variant_label: t.variant_label, subject: t.subject || '', body: t.body,
      weight: t.weight, is_active: t.is_active,
    });
    setPreview(null);
    setDialogOpen(true);
  };

  const runPreview = async () => {
    try {
      const res = await apiFetch('/api/email_templates/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ subject: form.subject || null, body: form.body }),
      });
      if (res.ok) setPreview(await res.json());
    } catch (e) {
      console.error(e);
    }
  };

  const insertVariable = (v: string) => setForm(f => ({ ...f, body: `${f.body}{{${v}}}` }));

  const save = async () => {
    if (!form.name.trim() || !form.body.trim()) {
      toast.error(txt('Name and body are required.', '名称和正文不能为空。'));
      return;
    }
    setIsSaving(true);
    try {
      const payload = {
        ...form,
        ab_group: form.ab_group.trim() || null,
        subject: form.subject.trim() || null,
        weight: Math.max(0, Math.min(100, Number(form.weight) || 1)),
      };
      const url = editingId ? `/api/email_templates/${editingId}` : '/api/email_templates/';
      const res = await apiFetch(url, {
        method: editingId ? 'PUT' : 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        toast.error(formatApiDetail(data.detail, txt('Save failed.', '保存失败。')));
        return;
      }
      toast.success(editingId ? txt('Template updated.', '模板已更新。') : txt('Template created.', '模板已创建。'));
      setDialogOpen(false);
      await fetchTemplates();
    } catch (e) {
      console.error(e);
      toast.error(txt('Network error.', '网络错误。'));
    } finally {
      setIsSaving(false);
    }
  };

  const doDelete = async () => {
    if (!deleteId) return;
    try {
      const res = await apiFetch(`/api/email_templates/${deleteId}`, { method: 'DELETE' });
      if (res.ok) { toast.success(txt('Template deleted.', '模板已删除。')); await fetchTemplates(); }
    } catch (e) {
      console.error(e);
    } finally {
      setDeleteId(null);
    }
  };

  // Group templates by ab_group for an A/B view; standalone ones get their own card.
  const groups: Record<string, EmailTemplate[]> = {};
  templates.forEach(t => {
    const key = t.ab_group ? `group:${t.ab_group}` : `solo:${t.id}`;
    (groups[key] ||= []).push(t);
  });

  return (
    <div>
      <div className="mb-6 flex items-end justify-between gap-4">
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-indigo-600">{txt('Outreach', '触达')}</p>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-950 sm:text-3xl">{txt('Email Templates', '邮件模板')}</h1>
          <p className="mt-2 text-sm text-slate-500">{txt('Reusable templates with variables and A/B variants. Reply rates are tracked per variant.', '可复用模板，支持变量与 A/B 变体，按变体统计回复率。')}</p>
        </div>
        <Button onClick={openCreate}><Plus className="mr-1 h-4 w-4" /> {txt('New Template', '新建模板')}</Button>
      </div>

      {isLoading ? (
        <div className="py-20 text-center text-slate-500">{txt('Loading...', '加载中...')}</div>
      ) : templates.length === 0 ? (
        <div className="rounded-lg border border-dashed border-slate-300 py-20 text-center text-slate-500">
          <FileText className="mx-auto mb-3 h-8 w-8 text-slate-300" />
          {txt('No templates yet. Create one to reuse across workflows.', '还没有模板。创建一个即可在工作流中复用。')}
        </div>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {Object.entries(groups).map(([key, items]) => {
            const isAb = key.startsWith('group:') && items.length > 1;
            return (
              <div key={key} className="glass-panel rounded-lg border border-slate-200 p-5">
                {isAb && (
                  <div className="mb-3 inline-flex items-center gap-1.5 rounded-full bg-purple-100 px-2.5 py-0.5 text-xs font-medium text-purple-700">
                    <FlaskConical className="h-3.5 w-3.5" /> {txt('A/B Test', 'A/B 测试')} · {items[0].ab_group}
                  </div>
                )}
                <div className="space-y-3">
                  {items.map(t => (
                    <div key={t.id} className="rounded-md border border-slate-100 bg-white p-3">
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0">
                          <div className="flex items-center gap-2">
                            {isAb && <span className="flex h-5 w-5 items-center justify-center rounded bg-indigo-500 text-[11px] font-bold text-white">{t.variant_label}</span>}
                            <span className="font-medium text-slate-900">{t.name}</span>
                            {!t.is_active && <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">{txt('inactive', '已停用')}</span>}
                          </div>
                          {t.subject && <div className="mt-1 truncate text-xs text-slate-500">{t.subject}</div>}
                        </div>
                        <div className="flex shrink-0 gap-1">
                          <button onClick={() => openEdit(t)} className="rounded p-1.5 text-slate-400 hover:bg-slate-100 hover:text-indigo-600" aria-label={txt('Edit', '编辑')}><Pencil className="h-4 w-4" /></button>
                          <button onClick={() => setDeleteId(t.id)} className="rounded p-1.5 text-slate-400 hover:bg-rose-50 hover:text-rose-600" aria-label={txt('Delete', '删除')}><Trash2 className="h-4 w-4" /></button>
                        </div>
                      </div>
                      {/* Per-variant reply-rate stats */}
                      <div className="mt-2 flex items-center gap-4 text-xs">
                        <span className="text-slate-500">{txt('Sent', '已发')}: <span className="font-semibold text-slate-700">{t.sent_count ?? 0}</span></span>
                        <span className="text-slate-500">{txt('Replied', '回复')}: <span className="font-semibold text-slate-700">{t.replied_count ?? 0}</span></span>
                        <span className="text-slate-500">{txt('Reply rate', '回复率')}: <span className="font-semibold text-emerald-600">{((t.reply_rate ?? 0) * 100).toFixed(1)}%</span></span>
                        {isAb && t.weight !== 1 && <span className="text-slate-400">{txt('weight', '权重')} {t.weight}</span>}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>{editingId ? txt('Edit Template', '编辑模板') : txt('New Template', '新建模板')}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label>{txt('Name', '名称')}</Label>
                <Input value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} />
              </div>
              <div className="space-y-1.5">
                <Label>{txt('Category', '类别')}</Label>
                <select value={form.category} onChange={e => setForm({ ...form, category: e.target.value })}
                  className="h-10 w-full rounded-md border border-slate-300 bg-white px-3 text-sm">
                  <option value="cold">{txt('Cold open', '开发信')}</option>
                  <option value="followup">{txt('Follow-up', '跟进')}</option>
                </select>
              </div>
            </div>

            <div className="grid grid-cols-3 gap-3">
              <div className="space-y-1.5">
                <Label>{txt('A/B group', 'A/B 分组')}</Label>
                <Input value={form.ab_group} placeholder={txt('optional', '可选')} onChange={e => setForm({ ...form, ab_group: e.target.value })} />
              </div>
              <div className="space-y-1.5">
                <Label>{txt('Variant', '变体')}</Label>
                <Input value={form.variant_label} onChange={e => setForm({ ...form, variant_label: e.target.value })} />
              </div>
              <div className="space-y-1.5">
                <Label>{txt('Weight', '权重')}</Label>
                <Input type="number" min={0} max={100} value={form.weight} onChange={e => setForm({ ...form, weight: parseInt(e.target.value) || 0 })} />
              </div>
            </div>
            <p className="-mt-2 text-xs text-slate-400">{txt('Templates sharing an A/B group are split by weight; reply rates are tracked per variant.', '同一 A/B 分组的模板按权重分流，回复率按变体分别统计。')}</p>

            <div className="space-y-1.5">
              <Label>{txt('Subject', '主题')}</Label>
              <Input value={form.subject} onChange={e => setForm({ ...form, subject: e.target.value })} />
            </div>

            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <Label>{txt('Body', '正文')}</Label>
                <div className="flex flex-wrap gap-1">
                  {VARIABLES.map(v => (
                    <button key={v} type="button" onClick={() => insertVariable(v)}
                      className="rounded border border-indigo-200 bg-indigo-50 px-1.5 py-0.5 text-[11px] text-indigo-600 hover:bg-indigo-100">
                      {`{{${v}}}`}
                    </button>
                  ))}
                </div>
              </div>
              <Textarea rows={8} value={form.body} onChange={e => setForm({ ...form, body: e.target.value })}
                placeholder={txt('Hi {{first_name}}, I noticed {{company_name}}...', '你好 {{first_name}}，我注意到 {{company_name}}...')} />
            </div>

            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={form.is_active} onChange={e => setForm({ ...form, is_active: e.target.checked })} className="h-4 w-4 accent-indigo-600" />
              {txt('Active (eligible for sending)', '启用（可用于发送）')}
            </label>

            {preview && (
              <div className="rounded-md border border-slate-200 bg-slate-50 p-3">
                <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">{txt('Preview (sample lead)', '预览（示例线索）')}</div>
                {preview.subject && <div className="text-sm font-medium text-slate-800">{preview.subject}</div>}
                <div className="mt-1 whitespace-pre-wrap text-sm text-slate-600">{preview.body}</div>
                {preview.unknown_variables.length > 0 && (
                  <div className="mt-2 text-xs text-amber-600">{txt('Unknown variables (render blank):', '未知变量（将渲染为空）：')} {preview.unknown_variables.map(v => `{{${v}}}`).join(', ')}</div>
                )}
              </div>
            )}

            <div className="flex justify-between gap-2 pt-1">
              <Button variant="outline" onClick={runPreview}><Eye className="mr-1 h-4 w-4" /> {txt('Preview', '预览')}</Button>
              <div className="flex gap-2">
                <Button variant="outline" onClick={() => setDialogOpen(false)}>{txt('Cancel', '取消')}</Button>
                <Button onClick={save} disabled={isSaving}>{isSaving ? txt('Saving...', '保存中...') : txt('Save', '保存')}</Button>
              </div>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={deleteId !== null}
        title={txt('Delete template?', '删除模板？')}
        message={txt('Workflows using it will fall back to AI generation.', '使用该模板的工作流将回退到 AI 生成。')}
        onConfirm={doDelete}
        onCancel={() => setDeleteId(null)}
      />
    </div>
  );
}
