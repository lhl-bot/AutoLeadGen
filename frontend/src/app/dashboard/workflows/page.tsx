"use client";

import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Briefcase, Plus, RefreshCw, Trash2, Play, Pause, ScrollText, MessageSquare, Search, Database, Mail, Gauge, Pencil, Globe2, Ship, Trophy, Store, Share2, FolderSearch, User } from 'lucide-react';
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
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { useTranslation } from '@/lib/i18n';
import ConfirmDialog from '@/components/ConfirmDialog';
import type { ClientPool, CustomerPersona, EmailAccount, Workflow, PlaybookPreset } from '@/lib/types';

interface WorkflowForm {
  name: string
  status: string
  search_keywords: string
  target_positions: string
  ai_prompt: string
  email_signature: string
  client_pool_id: string
  persona_id: string
  pilot_goal: string
  target_customer_type: string
  target_region: string
  product_focus: string
  manual_handoff_triggers: string
  search_sources: string
  competitor_names: string
  trade_show_names: string
  daily_limit: number
  send_interval_min: number
  send_interval_max: number
  auto_followup: boolean
  max_followups: number
  search_offset: number
  email_account_ids: number[]
  enable_linkedin: boolean
  enable_whatsapp: boolean
  linkedin_invite_message: string
  whatsapp_message_template: string
  linkedin_daily_limit: number
  playbook_type: string
}

const defaultWorkflowForm: WorkflowForm = {
  name: '',
  status: 'paused',
  search_keywords: '',
  target_positions: 'CEO, Owner, Purchasing Manager, Buyer',
  ai_prompt: '',
  email_signature: '',
  client_pool_id: 'none',
  persona_id: 'none',
  pilot_goal: '',
  target_customer_type: '',
  target_region: '',
  product_focus: '',
  manual_handoff_triggers: 'quote, sample, price, purchase plan, catalog, meeting',
  search_sources: 'web,directories,retail,social',
  competitor_names: '',
  trade_show_names: '',
  daily_limit: 50,
  send_interval_min: 60,
  send_interval_max: 300,
  auto_followup: false,
  max_followups: 3,
  search_offset: 0,
  email_account_ids: [],
  enable_linkedin: false,
  enable_whatsapp: false,
  linkedin_invite_message: '',
  whatsapp_message_template: '',
  linkedin_daily_limit: 20,
  playbook_type: 'standard'
}

function getDefaultWorkflowForm(): WorkflowForm {
  return { ...defaultWorkflowForm, email_account_ids: [] }
}

function workflowToForm(workflow: Workflow): WorkflowForm {
  return {
    name: workflow.name || '',
    status: workflow.status || 'paused',
    search_keywords: workflow.search_keywords || '',
    target_positions: workflow.target_positions || '',
    ai_prompt: workflow.ai_prompt || '',
    email_signature: workflow.email_signature || '',
    client_pool_id: workflow.client_pool_id ? workflow.client_pool_id.toString() : 'none',
    persona_id: workflow.persona_id ? workflow.persona_id.toString() : 'none',
    pilot_goal: workflow.pilot_goal || '',
    target_customer_type: workflow.target_customer_type || '',
    target_region: workflow.target_region || '',
    product_focus: workflow.product_focus || '',
    manual_handoff_triggers: workflow.manual_handoff_triggers || '',
    search_sources: workflow.search_sources || 'web,directories,retail,social',
    competitor_names: workflow.competitor_names || '',
    trade_show_names: workflow.trade_show_names || '',
    daily_limit: workflow.daily_limit || 50,
    send_interval_min: workflow.send_interval_min || 60,
    send_interval_max: workflow.send_interval_max || 300,
    auto_followup: Boolean(workflow.auto_followup),
    max_followups: workflow.max_followups || 3,
    search_offset: workflow.search_offset || 0,
    email_account_ids: workflow.emails?.map(email => email.id) || [],
    enable_linkedin: Boolean(workflow.enable_linkedin),
    enable_whatsapp: Boolean(workflow.enable_whatsapp),
    linkedin_invite_message: workflow.linkedin_invite_message || '',
    whatsapp_message_template: workflow.whatsapp_message_template || '',
    linkedin_daily_limit: workflow.linkedin_daily_limit || 20,
    playbook_type: workflow.playbook_type || 'standard'
  }
}

const searchSourceOptions = [
  { key: 'web', label: 'Web', icon: Globe2 },
  { key: 'customs', label: 'Customs', icon: Ship },
  { key: 'competitors', label: 'Competitors', icon: Trophy },
  { key: 'trade_shows', label: 'Trade Shows', icon: Briefcase },
  { key: 'directories', label: 'Directories', icon: FolderSearch },
  { key: 'retail', label: 'Retail', icon: Store },
  { key: 'social', label: 'Social', icon: Share2 },
]

export default function WorkflowsPage() {
  const { t } = useTranslation();
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  // Form dependencies
  const [pools, setPools] = useState<ClientPool[]>([]);
  const [personas, setPersonas] = useState<CustomerPersona[]>([]);
  const [emails, setEmails] = useState<EmailAccount[]>([]);

  // Workflow form state
  const [isWorkflowDialogOpen, setIsWorkflowDialogOpen] = useState(false);
  const [editingWorkflowId, setEditingWorkflowId] = useState<number | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [formData, setFormData] = useState<WorkflowForm>(getDefaultWorkflowForm());

  // Engine Logs State
  const [isLogsOpen, setIsLogsOpen] = useState(false);
  const [engineLogs, setEngineLogs] = useState('');
  const [isLogsLoading, setIsLogsLoading] = useState(false);
  const [searchingWorkflowId, setSearchingWorkflowId] = useState<number | null>(null);
  const [searchMessage, setSearchMessage] = useState('');
  const [togglingWorkflowId, setTogglingWorkflowId] = useState<number | null>(null);

  // Playbook state
  const [playbookPresets, setPlaybookPresets] = useState<PlaybookPreset[]>([]);
  const [showPlaybookSelector, setShowPlaybookSelector] = useState(false);
  const [isGeneratingKeywords, setIsGeneratingKeywords] = useState(false);

  // Delete confirmation state
  const [deleteDialog, setDeleteDialog] = useState(false);
  const [deleteTargetId, setDeleteTargetId] = useState<number | null>(null);

  const fetchWorkflows = async () => {
    setIsLoading(true);
    try {
      const res = await apiFetch('/api/workflows/');
      if (res.ok) {
        const data = await res.json();
        setWorkflows(data);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setIsLoading(false);
    }
  };

  const fetchDependencies = async () => {
    try {
      const [poolsRes, personasRes, emailsRes] = await Promise.all([
        apiFetch('/api/client_pools/'),
        apiFetch('/api/personas/'),
        apiFetch('/api/email_accounts/')
      ]);
      if (poolsRes.ok) setPools(await poolsRes.json());
      if (personasRes.ok) setPersonas(await personasRes.json());
      if (emailsRes.ok) setEmails(await emailsRes.json());
    } catch (e) {
      console.error('Failed to fetch dependencies', e);
    }
  };

  useEffect(() => {
    fetchWorkflows();
    fetchDependencies();
    // Fetch playbook presets
    apiFetch('/api/workflows/playbook-presets').then(async res => {
      if (res.ok) setPlaybookPresets(await res.json());
    }).catch(console.error);
  }, []);

  const openCreateDialog = () => {
    setEditingWorkflowId(null);
    setFormData(getDefaultWorkflowForm());
    setShowPlaybookSelector(true);
    setIsWorkflowDialogOpen(true);
  };

  const openEditDialog = (workflow: Workflow) => {
    setEditingWorkflowId(workflow.id);
    setFormData(workflowToForm(workflow));
    setShowPlaybookSelector(false);
    setIsWorkflowDialogOpen(true);
  };

  const handleWorkflowDialogOpenChange = (open: boolean) => {
    setIsWorkflowDialogOpen(open);
    if (!open) {
      setEditingWorkflowId(null);
      setFormData(getDefaultWorkflowForm());
      setShowPlaybookSelector(false);
    }
  };

  const selectPlaybook = (preset: PlaybookPreset) => {
    setFormData(prev => ({
      ...prev,
      name: preset.defaults.name_prefix || prev.name,
      ai_prompt: preset.defaults.ai_prompt || prev.ai_prompt,
      search_keywords: preset.defaults.search_keywords || prev.search_keywords,
      target_positions: preset.defaults.target_positions || prev.target_positions,
      daily_limit: preset.defaults.daily_limit || prev.daily_limit,
      send_interval_min: preset.defaults.send_interval_min || prev.send_interval_min,
      send_interval_max: preset.defaults.send_interval_max || prev.send_interval_max,
      auto_followup: preset.defaults.auto_followup ?? prev.auto_followup,
      max_followups: preset.defaults.max_followups || prev.max_followups,
      target_customer_type: preset.defaults.target_customer_type || prev.target_customer_type,
      target_region: preset.defaults.target_region || prev.target_region,
      product_focus: preset.defaults.product_focus || prev.product_focus,
      pilot_goal: preset.defaults.pilot_goal || prev.pilot_goal,
      manual_handoff_triggers: preset.defaults.manual_handoff_triggers || prev.manual_handoff_triggers,
      search_sources: preset.defaults.search_sources || prev.search_sources,
      competitor_names: preset.defaults.competitor_names || prev.competitor_names,
      trade_show_names: preset.defaults.trade_show_names || prev.trade_show_names,
      enable_linkedin: preset.defaults.enable_linkedin ?? prev.enable_linkedin,
      enable_whatsapp: preset.defaults.enable_whatsapp ?? prev.enable_whatsapp,
      linkedin_daily_limit: preset.defaults.linkedin_daily_limit || prev.linkedin_daily_limit,
      playbook_type: preset.key,
    }));
    setShowPlaybookSelector(false);
  };

  const buildWorkflowPayload = () => ({
    ...formData,
    client_pool_id: formData.client_pool_id === 'none' ? null : parseInt(formData.client_pool_id),
    persona_id: formData.persona_id === 'none' ? null : parseInt(formData.persona_id),
    daily_limit: Number(formData.daily_limit) || 50,
    send_interval_min: Number(formData.send_interval_min) || 60,
    send_interval_max: Number(formData.send_interval_max) || 300,
    max_followups: Number(formData.max_followups) || 3,
    search_offset: Number(formData.search_offset) || 0,
    linkedin_daily_limit: Number(formData.linkedin_daily_limit) || 20,
  });

  const handleSaveWorkflow = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsSaving(true);
    try {
      const payload = buildWorkflowPayload();
      const url = editingWorkflowId ? `/api/workflows/${editingWorkflowId}` : '/api/workflows/';
      const method = editingWorkflowId ? 'PUT' : 'POST';

      const res = await apiFetch(url, {
        method,
        body: JSON.stringify(payload)
      });
      if (res.ok) {
        setIsWorkflowDialogOpen(false);
        setEditingWorkflowId(null);
        fetchWorkflows();
        setFormData(getDefaultWorkflowForm());
      }
    } catch (e) {
      console.error(e);
    } finally {
      setIsSaving(false);
    }
  };

  const toggleWorkflow = async (id: number) => {
    setTogglingWorkflowId(id);
    try {
      const res = await apiFetch(`/api/workflows/${id}/toggle`, { method: 'POST' });
      if (!res.ok) {
        const errorData = await res.json().catch(() => ({}));
        const detail = errorData.detail || `Request failed (${res.status})`;
        toast.error(`${t('Operation failed')}: ${detail}`);
        return;
      }
      await fetchWorkflows();
    } catch(e) {
      console.error(e);
      toast.error(t('Network error'));
    } finally {
      setTogglingWorkflowId(null);
    }
  };

  const deleteWorkflow = async (id: number) => {
    setDeleteTargetId(id);
    setDeleteDialog(true);
  };

  const confirmDelete = async () => {
    if (deleteTargetId === null) return;
    const id = deleteTargetId;
    setDeleteDialog(false);
    setDeleteTargetId(null);
    try {
      const res = await apiFetch(`/api/workflows/${id}`, { method: 'DELETE' });
      if (!res.ok) {
        const errorData = await res.json().catch(() => ({}));
        const detail = errorData.detail || `Delete failed (${res.status})`;
        toast.error(`${t('Operation failed')}: ${detail}`);
        return;
      }
      await fetchWorkflows();
    } catch(e) {
      console.error(e);
      toast.error(t('Network error'));
    }
  };

  const startWorkflowSearch = async (id: number) => {
    setSearchingWorkflowId(id);
    setSearchMessage('');
    try {
      const res = await apiFetch(`/api/workflows/${id}/search`, { method: 'POST' });
      const data = await res.json();
      setSearchMessage(data.message || t('Search started'));
      window.setTimeout(fetchWorkflows, 4000);
    } catch (e) {
      console.error(e);
      setSearchMessage(t('Operation failed'));
    } finally {
      setSearchingWorkflowId(null);
    }
  };

  const loadEngineLogs = async () => {
    setIsLogsLoading(true);
    try {
      const res = await apiFetch('/api/engine_logs');
      if (res.ok) {
        const data = await res.json();
        setEngineLogs(data.logs || t('No logs yet'));
      }
    } catch {
      setEngineLogs('Error loading logs.');
    } finally {
      setIsLogsLoading(false);
    }
  };

  const handlePersonaSelect = (val: string | null) => {
    if (!val) {
      return
    }

    setFormData(prev => ({ ...prev, persona_id: val }));
    if (val !== 'none') {
      const p = personas.find(x => x.id === parseInt(val));
      if (p) {
        setFormData(prev => ({
          ...prev,
          search_keywords: p.target_keywords || prev.search_keywords,
          target_positions: p.target_roles || prev.target_positions,
          ai_prompt: p.ai_prompt_template || prev.ai_prompt,
          target_customer_type: p.customer_types || prev.target_customer_type,
          target_region: p.target_countries || prev.target_region,
          product_focus: p.product_categories || prev.product_focus
        }));
      }
    }
  };

  const handleSuggestKeywords = async () => {
    setIsGeneratingKeywords(true);
    try {
      const res = await apiFetch('/api/workflows/generate-keywords', {
        method: 'POST',
        body: JSON.stringify({
          persona_id: formData.persona_id === 'none' ? null : parseInt(formData.persona_id),
          description: (formData.name || '') + ' ' + (formData.ai_prompt || '') + ' ' + (formData.target_customer_type || '') + ' ' + (formData.product_focus || '')
        })
      });
      if (res.ok) {
        const data = await res.json();
        if (data.keywords && data.keywords.length > 0) {
          const generated = data.keywords.join(', ');
          setFormData(prev => ({ ...prev, search_keywords: generated }));
          toast.success('Successfully generated search keywords!');
        } else {
          toast.error('AI failed to generate keywords. Please enter manually.');
        }
      } else {
        const err = await res.json().catch(() => ({}));
        toast.error(err.detail || 'Failed to call AI keyword generator.');
      }
    } catch (e) {
      console.error(e);
      toast.error(t('Network error'));
    } finally {
      setIsGeneratingKeywords(false);
    }
  };

  const toggleEmail = (id: number) => {
    setFormData(prev => {
      const ids = prev.email_account_ids;
      if (ids.includes(id)) return { ...prev, email_account_ids: ids.filter(x => x !== id) };
      return { ...prev, email_account_ids: [...ids, id] };
    });
  };

  const toggleSearchSource = (source: string) => {
    setFormData(prev => {
      const selected = prev.search_sources.split(',').map(item => item.trim()).filter(Boolean);
      const next = selected.includes(source)
        ? selected.filter(item => item !== source)
        : [...selected, source];
      return { ...prev, search_sources: next.join(',') };
    });
  };

  const selectedSearchSources = formData.search_sources.split(',').map(item => item.trim()).filter(Boolean);

  return (
    <div>
      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:justify-between sm:items-end">
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-indigo-600">{t('Automation')}</p>
          <h1 className="text-2xl font-semibold tracking-tight text-white sm:text-3xl">{t('Workflows')}</h1>
          <p className="mt-2 text-sm text-gray-400">Set up automated pipelines for prospecting and outreach.</p>
        </div>
        <div className="flex flex-wrap gap-3">
          <Dialog open={isLogsOpen} onOpenChange={(open) => {
            setIsLogsOpen(open);
            if (open) loadEngineLogs();
          }}>
            <DialogTrigger asChild>
              <Button variant="outline" className="gap-2 bg-black/40 text-gray-300 border-white/20 hover:bg-black/60 hover:text-white">
                <ScrollText className="w-4 h-4" /> {t('Engine Logs')}
              </Button>
            </DialogTrigger>
            <DialogContent className="max-w-4xl bg-[#0d0d0f] border border-white/10 text-white">
              <DialogHeader>
                <DialogTitle className="flex justify-between pr-6">
                  <span>{t('Real-time Engine Logs')}</span>
                  <Button onClick={loadEngineLogs} variant="outline" size="sm" className="h-8 gap-2 bg-transparent border-white/20">
                    <RefreshCw className="w-3 h-3" /> {t('Refresh')}
                  </Button>
                </DialogTitle>
              </DialogHeader>
              <div className="mt-4 p-4 rounded-lg bg-black font-mono text-sm text-gray-400 h-[60vh] overflow-y-auto whitespace-pre-wrap">
                {isLogsLoading ? t('Loading...') : engineLogs}
              </div>
            </DialogContent>
          </Dialog>

          <Button onClick={fetchWorkflows} variant="outline" className="gap-2 bg-transparent text-slate-700 border-white/20">
            <RefreshCw className="w-4 h-4" /> {t('Refresh')}
          </Button>

          <Dialog open={isWorkflowDialogOpen} onOpenChange={handleWorkflowDialogOpenChange}>
            <DialogTrigger asChild>
              <Button onClick={openCreateDialog} className="gap-2 bg-indigo-600 hover:bg-indigo-700 text-white border-0">
                <Plus className="w-4 h-4" /> {t('New Workflow')}
              </Button>
            </DialogTrigger>
            <DialogContent className="sm:max-w-[700px] max-h-[90vh] overflow-y-auto">
              <DialogHeader>
                <DialogTitle>{editingWorkflowId ? t('Edit Workflow') : showPlaybookSelector ? t('Playbook') : t('New Workflow')}</DialogTitle>
              </DialogHeader>

              {/* ── Playbook Selector Step ── */}
              {showPlaybookSelector && !editingWorkflowId ? (
                <div className="mt-4">
                  <p className="text-sm text-muted-foreground mb-4">{t('Select a playbook to auto-fill the workflow settings.')}</p>
                  <div className="grid grid-cols-2 gap-3">
                    {playbookPresets.map(preset => (
                      <button
                        key={preset.key}
                        onClick={() => selectPlaybook(preset)}
                        className="group p-4 rounded-lg border border-border hover:border-indigo-500/50 hover:bg-indigo-500/5 transition-all text-left"
                      >
                        <div className="text-base font-semibold mb-1 group-hover:text-indigo-600 transition-colors">{preset.name}</div>
                        <p className="text-xs text-muted-foreground leading-relaxed">{preset.description}</p>
                      </button>
                    ))}
                  </div>
                  <button
                    onClick={() => setShowPlaybookSelector(false)}
                    className="mt-3 text-xs text-muted-foreground hover:text-foreground transition-colors"
                  >
                    {t('Skip, start from blank')}
                  </button>
                </div>
              ) : (

              <form onSubmit={handleSaveWorkflow} className="space-y-6 mt-4">
                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label>{t('Workflow Name')} *</Label>
                    <Input required value={formData.name} onChange={e => setFormData({...formData, name: e.target.value})} placeholder={t('Enter a name for this workflow')} />
                  </div>
                  <div className="space-y-2">
                    <Label>{t('Customer Persona')} (Auto-fill)</Label>
                    <Select value={formData.persona_id} onValueChange={handlePersonaSelect}>
                      <SelectTrigger>
                        <SelectValue placeholder={t('Select a persona...')} />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="none">-- Unbound --</SelectItem>
                        {personas.map(p => <SelectItem key={p.id} value={p.id.toString()}>{p.name}</SelectItem>)}
                      </SelectContent>
                    </Select>
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <div className="flex items-center justify-between">
                      <Label>{t('Search Keywords')} *</Label>
                      <button
                        type="button"
                        onClick={handleSuggestKeywords}
                        disabled={isGeneratingKeywords}
                        className="text-xs text-indigo-400 hover:text-indigo-300 disabled:opacity-50 transition-colors flex items-center gap-1"
                      >
                        {isGeneratingKeywords ? t('Generating...') : t('Generate Keywords (AI)')}
                      </button>
                    </div>
                    <Input required value={formData.search_keywords} onChange={e => setFormData({...formData, search_keywords: e.target.value})} placeholder="e.g. Padel equipment Europe" />
                  </div>
                  <div className="space-y-2">
                    <Label>{t('Target Positions')} *</Label>
                    <Input required value={formData.target_positions} onChange={e => setFormData({...formData, target_positions: e.target.value})} />
                  </div>
                </div>

                <div className="space-y-3 rounded-lg border border-border p-4">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <Label>{t('Search Sources')}</Label>
                      <p className="mt-1 text-xs text-muted-foreground">Use multiple buyer pools instead of relying on one keyword search.</p>
                    </div>
                    <Badge variant="outline">{selectedSearchSources.length} selected</Badge>
                  </div>
                  <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                    {searchSourceOptions.map(option => {
                      const Icon = option.icon;
                      const checked = selectedSearchSources.includes(option.key);
                      return (
                        <button
                          key={option.key}
                          type="button"
                          onClick={() => toggleSearchSource(option.key)}
                          className={`flex h-10 items-center gap-2 rounded-md border px-3 text-sm transition-colors ${checked ? 'border-indigo-500 bg-indigo-500/10 text-indigo-600' : 'border-border text-muted-foreground hover:bg-muted'}`}
                          aria-pressed={checked}
                        >
                          <Icon className="h-4 w-4" />
                          {option.label}
                        </button>
                      );
                    })}
                  </div>
                  <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                    <div className="space-y-2">
                      <Label>{t('Competitor Names')}</Label>
                      <Input value={formData.competitor_names} onChange={e => setFormData({...formData, competitor_names: e.target.value})} placeholder="Bullpadel, Nox, Head" />
                    </div>
                    <div className="space-y-2">
                      <Label>{t('Trade Show Names')}</Label>
                      <Input value={formData.trade_show_names} onChange={e => setFormData({...formData, trade_show_names: e.target.value})} placeholder="ISPO, MAGIC, Outdoor Retailer" />
                    </div>
                  </div>
                </div>

                <div className="space-y-2">
                  <Label>{t('AI Prompt')}</Label>
                  <Textarea value={formData.ai_prompt} onChange={e => setFormData({...formData, ai_prompt: e.target.value})} placeholder="Instructions for AI drafting..." />
                </div>

                <div className="border-t pt-4">
                  <h4 className="text-sm font-medium text-foreground/80 mb-4">Pilot & Qualification</h4>
                  <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
                    <div className="space-y-2">
                      <Label>{t('Target Customer Type')}</Label>
                      <Input value={formData.target_customer_type} onChange={e => setFormData({...formData, target_customer_type: e.target.value})} placeholder="distributor, agent, brand" />
                    </div>
                    <div className="space-y-2">
                      <Label>{t('Target Region')}</Label>
                      <Input value={formData.target_region} onChange={e => setFormData({...formData, target_region: e.target.value})} placeholder="Europe, Germany, ASEAN" />
                    </div>
                    <div className="space-y-2">
                      <Label>{t('Product Focus')}</Label>
                      <Input value={formData.product_focus} onChange={e => setFormData({...formData, product_focus: e.target.value})} placeholder="skiwear, functional apparel" />
                    </div>
                  </div>
                  <div className="grid grid-cols-1 gap-4 mt-4 sm:grid-cols-2">
                    <div className="space-y-2">
                      <Label>{t('Pilot Goal')}</Label>
                      <Textarea value={formData.pilot_goal} onChange={e => setFormData({...formData, pilot_goal: e.target.value})} className="min-h-[90px]" placeholder="1-3 month validation goal and success criteria..." />
                    </div>
                    <div className="space-y-2">
                      <Label>{t('Manual Handoff Triggers')}</Label>
                      <Textarea value={formData.manual_handoff_triggers} onChange={e => setFormData({...formData, manual_handoff_triggers: e.target.value})} className="min-h-[90px]" placeholder="quote, sample, MOQ, purchase plan, meeting..." />
                    </div>
                  </div>
                </div>

                <div className="space-y-2">
                  <Label>{t('Email Signature')}</Label>
                  <Textarea value={formData.email_signature} onChange={e => setFormData({...formData, email_signature: e.target.value})} placeholder="Best regards,..." />
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label>{t('Client Pool')}</Label>
                    <Select value={formData.client_pool_id} onValueChange={v => setFormData({...formData, client_pool_id: v || 'none'})}>
                      <SelectTrigger>
                        <SelectValue placeholder={t('Select a client pool...')} />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="none">-- Unbound --</SelectItem>
                        {pools.map(p => <SelectItem key={p.id} value={p.id.toString()}>{p.name}</SelectItem>)}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-2">
                    <Label>{t('Email Accounts')}</Label>
                    <div className="h-10 px-3 py-2 rounded-md border border-input bg-background flex gap-2 overflow-x-auto items-center">
                      {emails.map(em => (
                        <label key={em.id} className="flex items-center gap-1.5 text-sm whitespace-nowrap cursor-pointer">
                          <input type="checkbox" checked={formData.email_account_ids.includes(em.id)} onChange={() => toggleEmail(em.id)} className="accent-indigo-500" />
                          {em.email}
                        </label>
                      ))}
                      {emails.length === 0 && <span className="text-muted-foreground text-xs">{t('No email accounts added yet.')}</span>}
                    </div>
                  </div>
                </div>

                <div className="border-t pt-4">
                  <h4 className="text-sm font-medium text-foreground/80 mb-4">Advanced Settings</h4>
                  <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                    <div className="space-y-2">
                      <Label>{t('Daily Limit')}</Label>
                      <Input type="number" required value={formData.daily_limit} onChange={e => setFormData({...formData, daily_limit: parseInt(e.target.value)})} />
                    </div>
                    <div className="space-y-2">
                      <Label>{t('Send Interval Min (s)')}</Label>
                      <Input type="number" required value={formData.send_interval_min} onChange={e => setFormData({...formData, send_interval_min: parseInt(e.target.value)})} />
                    </div>
                    <div className="space-y-2">
                      <Label>{t('Send Interval Max (s)')}</Label>
                      <Input type="number" required value={formData.send_interval_max} onChange={e => setFormData({...formData, send_interval_max: parseInt(e.target.value)})} />
                    </div>
                    <div className="space-y-2">
                      <Label>{t('Max Follow-ups')}</Label>
                      <Input type="number" required value={formData.max_followups} onChange={e => setFormData({...formData, max_followups: parseInt(e.target.value) || 3})} />
                    </div>
                  </div>
                  <label className="flex items-center gap-2 mt-4 text-sm cursor-pointer">
                    <input type="checkbox" checked={formData.auto_followup} onChange={e => setFormData({...formData, auto_followup: e.target.checked})} className="accent-indigo-500 w-4 h-4" />
                    {t('Auto Follow-up')} (AI drafts sent automatically)
                  </label>
                </div>

                {/* Omnichannel Settings */}
                <div className="border-t pt-4">
                  <h4 className="text-sm font-medium text-foreground/80 mb-4">Channel Settings</h4>
                  <div className="space-y-4">
                    <div className="flex items-start gap-4">
                      <label className="flex items-center gap-2 text-sm cursor-pointer min-w-[200px] pt-1">
                        <input type="checkbox" checked={formData.enable_linkedin} onChange={e => setFormData({...formData, enable_linkedin: e.target.checked})} className="accent-blue-500 w-4 h-4" />
                        <span className="font-bold text-blue-600 text-xs">in</span> {t('Enable LinkedIn')}
                      </label>
                      {formData.enable_linkedin && (
                        <div className="flex-1 space-y-2">
                          <Textarea value={formData.linkedin_invite_message} onChange={e => setFormData({...formData, linkedin_invite_message: e.target.value})} className="text-sm" placeholder="AI prompt for LinkedIn invite (optional, uses email prompt if empty)" rows={2} />
                          <div className="flex items-center gap-2">
                            <Label className="text-xs text-muted-foreground">{t('LinkedIn Daily Limit')}:</Label>
                            <Input type="number" value={formData.linkedin_daily_limit} onChange={e => setFormData({...formData, linkedin_daily_limit: parseInt(e.target.value) || 20})} className="w-20 h-7 text-sm" />
                          </div>
                        </div>
                      )}
                    </div>
                    <div className="flex items-start gap-4">
                      <label className="flex items-center gap-2 text-sm cursor-pointer min-w-[200px] pt-1">
                        <input type="checkbox" checked={formData.enable_whatsapp} onChange={e => setFormData({...formData, enable_whatsapp: e.target.checked})} className="accent-green-500 w-4 h-4" />
                        <MessageSquare className="w-4 h-4 text-emerald-600" /> {t('Enable WhatsApp')}
                      </label>
                      {formData.enable_whatsapp && (
                        <div className="flex-1">
                          <Textarea value={formData.whatsapp_message_template} onChange={e => setFormData({...formData, whatsapp_message_template: e.target.value})} className="text-sm" placeholder="AI prompt for WhatsApp message (optional)" rows={2} />
                        </div>
                      )}
                    </div>
                  </div>
                </div>

                <div className="pt-4 flex justify-end">
                  <Button type="submit" disabled={isSaving} className="bg-indigo-600 hover:bg-indigo-700 text-white">
                    {isSaving ? 'Saving...' : editingWorkflowId ? t('Edit Workflow') : t('Save')}
                  </Button>
                </div>
              </form>
              )}
            </DialogContent>
          </Dialog>
        </div>
      </div>
      {searchMessage && (
        <div className="mb-4 rounded-lg border border-emerald-500/20 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-700">
          {searchMessage}
        </div>
      )}

      {isLoading ? (
        <div className="py-20 text-center text-gray-500">{t('Loading workflows...')}</div>
      ) : workflows.length === 0 ? (
        <div className="glass-panel p-12 text-center text-gray-400 rounded-lg border border-dashed border-white/20">
          <Briefcase className="w-12 h-12 mx-auto mb-4 opacity-50" />
          <p>{t('No workflows created yet.')} {t('Create your first workflow to start finding leads.')}</p>
        </div>
      ) : (
        <div className="grid gap-4">
          {workflows.map(wf => {
            const isActive = wf.status === 'active';
            const sources = (wf.search_sources || 'web').split(',').map(item => item.trim()).filter(Boolean);
            return (
              <div key={wf.id} className={`glass-panel p-5 rounded-lg flex flex-col gap-5 xl:flex-row xl:items-center xl:justify-between transition-all ${isActive ? 'border-indigo-500/50 shadow-[0_12px_32px_rgba(79,70,229,0.12)]' : 'border-white/5 opacity-80'}`}>
                <div className="flex-1">
                  <div className="flex items-center gap-3 mb-2">
                    <h3 className="font-bold text-lg text-white">{wf.name}</h3>
                    <Badge variant="outline" className={isActive ? 'bg-indigo-500/20 text-indigo-500 border-indigo-500/50' : 'text-gray-400 border-gray-600'}>
                      {isActive ? t('Active') : t('Paused')}
                    </Badge>
                  </div>
                  <div className="text-sm text-gray-400 mb-3 flex items-center gap-2">
                    <Search className="h-4 w-4 text-indigo-500" />
                    {wf.search_keywords || '—'}
                  </div>

                  <div className="flex flex-wrap gap-2">
                    {wf.client_pool_name && (
                      <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-white/5 text-gray-300">
                        <Database className="h-3.5 w-3.5" /> {wf.client_pool_name}
                      </span>
                    )}
                    {(() => {
                      const persona = personas.find(p => p.id === wf.persona_id);
                      return persona ? (
                        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-purple-500/10 text-purple-400">
                          <User className="h-3.5 w-3.5" /> {t('Customer Personas')}: {persona.name}
                        </span>
                      ) : null;
                    })()}
                    <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-emerald-500/10 text-emerald-500">
                      <Mail className="h-3.5 w-3.5" /> {wf.emails?.length || 0} {t('Email Accounts')}
                    </span>
                    {wf.enable_linkedin && (
                      <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-blue-500/10 text-blue-600">
                        <Briefcase className="h-3.5 w-3.5" /> LinkedIn
                      </span>
                    )}
                    {wf.enable_whatsapp && (
                      <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-green-500/10 text-emerald-500">
                        <MessageSquare className="h-3.5 w-3.5" /> WhatsApp
                      </span>
                    )}
                    {wf.email_paused && (
                      <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-rose-500/10 text-rose-400">
                        {t('Paused')} · {t('Bounce Rate')} {Math.round((wf.bounce_rate || 0) * 100)}%
                      </span>
                    )}
                    {wf.playbook_type && (
                      <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-white/5 text-gray-300">
                        {wf.playbook_type}
                      </span>
                    )}
                    {sources.slice(0, 4).map(source => (
                      <span key={source} className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-cyan-500/10 text-cyan-300">
                        {source}
                      </span>
                    ))}
                    {sources.length > 4 && (
                      <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-cyan-500/10 text-cyan-300">
                        +{sources.length - 4}
                      </span>
                    )}
                    {(wf.avg_fit_score || 0) > 0 && (
                      <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-sky-500/10 text-sky-400">
                        Fit {Math.round(wf.avg_fit_score || 0)}/100
                      </span>
                    )}
                    {(wf.handoff_count || 0) > 0 && (
                      <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-amber-500/10 text-amber-400">
                        {wf.handoff_count} handoff
                      </span>
                    )}
                    <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-orange-500/10 text-orange-500">
                      <Gauge className="h-3.5 w-3.5" /> {t('Daily Limit')} {wf.daily_limit}/day | {wf.send_interval_min}-{wf.send_interval_max}s
                    </span>
                  </div>
                </div>

                <div className="flex flex-col gap-4 border-t border-white/10 pt-4 xl:ml-6 xl:border-l xl:border-t-0 xl:pl-6 xl:pt-0">
                  <div className="grid grid-cols-4 gap-5 text-center">
                    <div className="flex flex-col items-center">
                      <span className="text-2xl font-bold text-white">{wf.leads_count || 0}</span>
                      <span className="text-xs text-gray-500 uppercase">{t('Total')}</span>
                    </div>
                    <div className="flex flex-col items-center">
                      <span className="text-2xl font-bold text-emerald-400">{wf.contactable_count || 0}</span>
                      <span className="text-xs text-gray-500 uppercase">{t('Contactable')}</span>
                    </div>
                    <div className="flex flex-col items-center">
                      <span className="text-2xl font-bold text-amber-400">{wf.needs_email_count || 0}</span>
                      <span className="text-xs text-gray-500 uppercase">{t('Needs Email')}</span>
                    </div>
                    <div className="flex flex-col items-center">
                      <span className="text-2xl font-bold text-orange-400">{wf.low_score_count || 0}</span>
                      <span className="text-xs text-gray-500 uppercase">{t('Low Score')}</span>
                    </div>
                  </div>

                  <div className="flex flex-wrap items-center gap-2 xl:justify-end">
                    <Button
                      onClick={() => openEditDialog(wf)}
                      variant="outline"
                      className="gap-2 w-24 bg-transparent border-white/20 text-gray-200 hover:bg-white/10"
                    >
                      <Pencil className="w-4 h-4" /> {t('Edit')}
                    </Button>
                    <Button
                      onClick={() => startWorkflowSearch(wf.id)}
                      disabled={searchingWorkflowId === wf.id}
                      variant="outline"
                      className="gap-2 w-32 bg-transparent border-white/20 text-gray-200 hover:bg-white/10"
                    >
                      <Search className="w-4 h-4" /> {searchingWorkflowId === wf.id ? 'Finding...' : 'Find Leads'}
                    </Button>
                    <Button
                      onClick={() => toggleWorkflow(wf.id)}
                      disabled={togglingWorkflowId === wf.id}
                      variant={isActive ? "secondary" : "default"}
                      className={`gap-2 w-28 ${isActive ? 'bg-white/10 text-slate-700 hover:bg-white/20' : 'bg-indigo-600 hover:bg-indigo-700 text-white'}`}
                    >
                      {togglingWorkflowId === wf.id ? (
                        <><RefreshCw className="w-4 h-4 animate-spin" /> {t('Switching...')}</>
                      ) : isActive ? (
                        <><Pause className="w-4 h-4" /> {t('Stop')}</>
                      ) : (
                        <><Play className="w-4 h-4" /> {t('Start')}</>
                      )}
                    </Button>
                    <Button onClick={() => deleteWorkflow(wf.id)} variant="ghost" size="icon" className="text-gray-500 hover:text-rose-500 hover:bg-red-400/10">
                      <Trash2 className="w-4 h-4" />
                    </Button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      <ConfirmDialog
        open={deleteDialog}
        title={t('Confirm Delete')}
        message={t('Are you sure you want to delete this workflow and all its leads?')}
        onConfirm={confirmDelete}
        onCancel={() => { setDeleteDialog(false); setDeleteTargetId(null); }}
      />
    </div>
  );
}
