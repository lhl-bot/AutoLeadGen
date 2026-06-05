"use client";

import { useCallback, useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Users, Plus, RefreshCw, Trash2, Target, Pencil } from 'lucide-react';
import { apiFetch } from '@/lib/utils';
import { toast } from 'sonner';
import { useTranslation } from '@/lib/i18n';
import ConfirmDialog from '@/components/ConfirmDialog';
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
import type { CustomerPersona } from '@/lib/types';

interface PersonaForm {
  name: string
  target_industry: string
  target_countries: string
  target_roles: string
  target_keywords: string
  negative_keywords: string
  ai_prompt_template: string
  customer_types: string
  product_categories: string
  evidence_sources: string
  qualification_rules: string
  disqualification_rules: string
  cultural_notes: string
  positive_examples: string
  negative_examples: string
}

const emptyPersonaForm: PersonaForm = {
  name: '',
  target_industry: '',
  target_countries: '',
  target_roles: '',
  target_keywords: '',
  negative_keywords: '',
  ai_prompt_template: '',
  customer_types: '',
  product_categories: '',
  evidence_sources: 'website, social media, customs data, historical feedback',
  qualification_rules: '',
  disqualification_rules: '',
  cultural_notes: '',
  positive_examples: '',
  negative_examples: ''
}

function personaToForm(persona: CustomerPersona): PersonaForm {
  return {
    name: persona.name || '',
    target_industry: persona.target_industry || '',
    target_countries: persona.target_countries || '',
    target_roles: persona.target_roles || '',
    target_keywords: persona.target_keywords || '',
    negative_keywords: persona.negative_keywords || '',
    ai_prompt_template: persona.ai_prompt_template || '',
    customer_types: persona.customer_types || '',
    product_categories: persona.product_categories || '',
    evidence_sources: persona.evidence_sources || '',
    qualification_rules: persona.qualification_rules || '',
    disqualification_rules: persona.disqualification_rules || '',
    cultural_notes: persona.cultural_notes || '',
    positive_examples: persona.positive_examples || '',
    negative_examples: persona.negative_examples || ''
  }
}

export default function PersonasPage() {
  const { t } = useTranslation();
  const [personas, setPersonas] = useState<CustomerPersona[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  // Dialog state
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [editingPersona, setEditingPersona] = useState<CustomerPersona | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [formData, setFormData] = useState<PersonaForm>(emptyPersonaForm);

  // Delete confirmation state
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [deleteId, setDeleteId] = useState<number | null>(null);

  const fetchPersonas = useCallback(async () => {
    setIsLoading(true);
    try {
      const res = await apiFetch('/api/personas/');
      if (res.ok) {
        const data = await res.json();
        setPersonas(data);
      }
    } catch (e) {
      console.error(e);
      toast.error(t('Network error'));
    } finally {
      setIsLoading(false);
    }
  }, [t]);

  useEffect(() => {
    fetchPersonas();
  }, [fetchPersonas]);

  const openCreateDialog = () => {
    setEditingPersona(null);
    setFormData(emptyPersonaForm);
    setIsDialogOpen(true);
  };

  const openEditDialog = (persona: CustomerPersona) => {
    setEditingPersona(persona);
    setFormData(personaToForm(persona));
    setIsDialogOpen(true);
  };

  const handleDialogOpenChange = (open: boolean) => {
    setIsDialogOpen(open);
    if (!open) {
      setEditingPersona(null);
      setFormData(emptyPersonaForm);
    }
  };

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();

    // Form validation
    if (!formData.name.trim()) {
      toast.error(t('Persona Name') + ' ' + t('is required'));
      return;
    }

    setIsSaving(true);
    try {
      const isEdit = !!editingPersona;
      const url = isEdit
        ? `/api/personas/${editingPersona.id}`
        : '/api/personas/';
      const method = isEdit ? 'PUT' : 'POST';

      const res = await apiFetch(url, {
        method,
        body: JSON.stringify(formData)
      });

      if (res.ok) {
        setIsDialogOpen(false);
        setEditingPersona(null);
        setFormData(emptyPersonaForm);
        fetchPersonas();
        toast.success(isEdit ? t('Persona updated') : t('Persona created'));
      } else {
        const err = await res.json().catch(() => ({}));
        toast.error(err.detail || t('Operation failed'));
      }
    } catch (e) {
      console.error(e);
      toast.error(t('Network error'));
    } finally {
      setIsSaving(false);
    }
  };

  const openDeleteDialog = (id: number) => {
    setDeleteId(id);
    setDeleteDialogOpen(true);
  };

  const confirmDelete = async () => {
    if (deleteId === null) return;
    try {
      const res = await apiFetch(`/api/personas/${deleteId}`, { method: 'DELETE' });
      if (res.ok) {
        fetchPersonas();
        toast.success(t('Persona deleted'));
      } else {
        const err = await res.json().catch(() => ({}));
        toast.error(err.detail || t('Operation failed'));
      }
    } catch (e) {
      console.error(e);
      toast.error(t('Network error'));
    } finally {
      setDeleteDialogOpen(false);
      setDeleteId(null);
    }
  };

  const isEditMode = !!editingPersona;

  return (
    <div>
      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:justify-between sm:items-end">
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-indigo-600">{t('WORKSPACE')}</p>
          <h1 className="text-2xl font-semibold tracking-tight text-white sm:text-3xl">{t('Customer Personas')}</h1>
          <p className="mt-2 text-sm text-gray-400">{t('Define your ideal customer profiles for better AI personalization.')}</p>
        </div>
        <div className="flex flex-wrap gap-3">
          <Button onClick={fetchPersonas} variant="outline" className="gap-2 bg-transparent text-slate-700 border-white/20">
            <RefreshCw className="w-4 h-4" /> {t('Refresh')}
          </Button>

          <Dialog open={isDialogOpen} onOpenChange={handleDialogOpenChange}>
            <DialogTrigger asChild>
              <Button onClick={openCreateDialog} className="gap-2 bg-indigo-600 hover:bg-indigo-700 text-white border-0">
                <Plus className="w-4 h-4" /> {t('New Persona')}
              </Button>
            </DialogTrigger>
            <DialogContent className="sm:max-w-[600px] max-h-[90vh] overflow-y-auto">
              <DialogHeader>
                <DialogTitle>{isEditMode ? t('Edit Persona') : t('New Persona')}</DialogTitle>
              </DialogHeader>
              <form onSubmit={handleSave} className="space-y-4 mt-4">
                <div className="space-y-2">
                  <Label>{t('Persona Name')} *</Label>
                  <Input required value={formData.name} onChange={e => setFormData({...formData, name: e.target.value})} placeholder={t('e.g. European Padel Retailers')} />
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label>{t('Target Industry')}</Label>
                    <Input value={formData.target_industry} onChange={e => setFormData({...formData, target_industry: e.target.value})} placeholder={t('e.g. Sports Equipment')} />
                  </div>
                  <div className="space-y-2">
                    <Label>{t('Target Countries')}</Label>
                    <Input value={formData.target_countries} onChange={e => setFormData({...formData, target_countries: e.target.value})} placeholder={t('e.g. Spain, Italy')} />
                  </div>
                </div>

                <div className="space-y-2">
                  <Label>{t('Target Roles')}</Label>
                  <Input value={formData.target_roles} onChange={e => setFormData({...formData, target_roles: e.target.value})} placeholder={t('e.g. CEO, Founder, Buyer')} />
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label>{t('Customer Types')}</Label>
                    <Input value={formData.customer_types} onChange={e => setFormData({...formData, customer_types: e.target.value})} placeholder={t('e.g. distributor, agent, brand')} />
                  </div>
                  <div className="space-y-2">
                    <Label>{t('Product Categories')}</Label>
                    <Input value={formData.product_categories} onChange={e => setFormData({...formData, product_categories: e.target.value})} placeholder={t('e.g. skiwear, functional apparel')} />
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label>{t('Search Keywords')}</Label>
                    <Input value={formData.target_keywords} onChange={e => setFormData({...formData, target_keywords: e.target.value})} placeholder={t('e.g. padel store')} />
                  </div>
                  <div className="space-y-2">
                    <Label>{t('Negative Keywords')}</Label>
                    <Input value={formData.negative_keywords} onChange={e => setFormData({...formData, negative_keywords: e.target.value})} placeholder={t('e.g. manufacturer, factory')} />
                  </div>
                </div>

                <div className="space-y-2">
                  <Label>{t('Evidence Sources')}</Label>
                  <Input value={formData.evidence_sources} onChange={e => setFormData({...formData, evidence_sources: e.target.value})} placeholder={t('e.g. website, social media, customs data, trade show list')} />
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label>{t('Qualification Rules')}</Label>
                    <Textarea value={formData.qualification_rules} onChange={e => setFormData({...formData, qualification_rules: e.target.value})} className="min-h-[90px]" placeholder={t('Signals that make a lead a good fit...')} />
                  </div>
                  <div className="space-y-2">
                    <Label>{t('Disqualification Rules')}</Label>
                    <Textarea value={formData.disqualification_rules} onChange={e => setFormData({...formData, disqualification_rules: e.target.value})} className="min-h-[90px]" placeholder={t('Signals to avoid, e.g. factory, supplier, job board...')} />
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label>{t('Positive Examples')}</Label>
                    <Textarea value={formData.positive_examples} onChange={e => setFormData({...formData, positive_examples: e.target.value})} className="min-h-[80px]" placeholder={t('Known customers or ideal company examples...')} />
                  </div>
                  <div className="space-y-2">
                    <Label>{t('Negative Examples')}</Label>
                    <Textarea value={formData.negative_examples} onChange={e => setFormData({...formData, negative_examples: e.target.value})} className="min-h-[80px]" placeholder={t('Companies that should be filtered out...')} />
                  </div>
                </div>

                <div className="space-y-2">
                  <Label>{t('Cultural Notes')}</Label>
                  <Textarea value={formData.cultural_notes} onChange={e => setFormData({...formData, cultural_notes: e.target.value})} className="min-h-[80px]" placeholder={t('e.g. Europe values quality/certifications; Southeast Asia is more price-sensitive.')} />
                </div>

                <div className="space-y-2">
                  <Label>{t('AI Prompt Template')}</Label>
                  <Textarea value={formData.ai_prompt_template} onChange={e => setFormData({...formData, ai_prompt_template: e.target.value})} className="min-h-[100px]" placeholder={t('Tell AI how to pitch this persona... e.g. Focus on ROI and quick shipping times.')} />
                </div>

                <div className="pt-4 flex justify-end">
                  <Button type="submit" disabled={isSaving} className="bg-indigo-600 hover:bg-indigo-700 text-white">
                    {isSaving ? t('Saving...') : t('Save')}
                  </Button>
                </div>
              </form>
            </DialogContent>
          </Dialog>
        </div>
      </div>

      {isLoading ? (
        <div className="py-20 text-center text-gray-500">{t('Loading personas...')}</div>
      ) : personas.length === 0 ? (
        <div className="glass-panel p-12 text-center text-gray-400 rounded-lg border border-dashed border-white/20">
          <Users className="w-12 h-12 mx-auto mb-4 opacity-50" />
          <p>{t('No personas created yet.')} {t('Create your first persona to start targeting ideal customers.')}</p>
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {personas.map(persona => (
            <div key={persona.id} className="glass-panel p-5 rounded-lg flex flex-col justify-between hover:border-indigo-500/50 hover:shadow-[0_12px_32px_rgba(79,70,229,0.12)] transition-all">
              <div>
                <div className="flex justify-between items-start mb-2">
                  <h3 className="font-bold text-lg text-white">{persona.name}</h3>
                  <div className="flex items-center gap-1">
                    <button
                      onClick={() => openEditDialog(persona)}
                      className="text-gray-500 hover:text-indigo-400 transition-colors"
                      title={t('Edit')}
                    >
                      <Pencil className="w-4 h-4" />
                    </button>
                    <button
                      onClick={() => openDeleteDialog(persona.id)}
                      className="text-gray-500 hover:text-red-400 transition-colors"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>
                <div className="text-sm text-gray-400 mb-4 flex items-center gap-2">
                  <Target className="w-4 h-4" /> {persona.target_industry || t('Any Industry')} | {persona.target_countries || t('Global')}
                </div>

                <div className="space-y-3 mt-4 pt-4 border-t border-white/10 text-sm">
                  <div>
                    <span className="text-gray-500 block mb-1">{t('Target Roles')}</span>
                    <span className="text-gray-200">{persona.target_roles || '—'}</span>
                  </div>
                  <div>
                    <span className="text-gray-500 block mb-1">{t('Customer Types')}</span>
                    <span className="text-gray-200">{persona.customer_types || '—'}</span>
                  </div>
                  <div>
                    <span className="text-gray-500 block mb-1">{t('Product Categories')}</span>
                    <span className="text-gray-200">{persona.product_categories || '—'}</span>
                  </div>
                  <div>
                    <span className="text-gray-500 block mb-1">{t('Search Keywords')}</span>
                    <span className="text-indigo-600">{persona.target_keywords || '—'}</span>
                  </div>
                  {persona.evidence_sources && (
                    <div>
                      <span className="text-gray-500 block mb-1">{t('Evidence Sources')}</span>
                      <span className="text-gray-300">{persona.evidence_sources}</span>
                    </div>
                  )}
                  {persona.negative_keywords && (
                    <div>
                      <span className="text-gray-500 block mb-1">{t('Negative Keywords')}</span>
                      <span className="text-rose-400/80">{persona.negative_keywords}</span>
                    </div>
                  )}
                  {(persona.qualification_rules || persona.disqualification_rules) && (
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <span className="text-gray-500 block mb-1">{t('Qualification Rules')}</span>
                        <span className="text-gray-300">{persona.qualification_rules || '—'}</span>
                      </div>
                      <div>
                        <span className="text-gray-500 block mb-1">{t('Disqualification Rules')}</span>
                        <span className="text-rose-300/80">{persona.disqualification_rules || '—'}</span>
                      </div>
                    </div>
                  )}
                  {persona.cultural_notes && (
                    <div>
                      <span className="text-gray-500 block mb-1">{t('Cultural Notes')}</span>
                      <span className="text-gray-300">{persona.cultural_notes}</span>
                    </div>
                  )}
                  {persona.ai_prompt_template && (
                    <div className="mt-4 p-3 bg-white/5 border-l-2 border-indigo-500 rounded text-gray-300 text-xs">
                      <strong>{t('AI Prompt Template')}:</strong> {persona.ai_prompt_template}
                    </div>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      <ConfirmDialog
        open={deleteDialogOpen}
        title={t('Confirm Delete')}
        message={t('Are you sure you want to delete this persona?')}
        confirmLabel={t('Yes, delete')}
        cancelLabel={t('Cancel')}
        onConfirm={confirmDelete}
        onCancel={() => {
          setDeleteDialogOpen(false);
          setDeleteId(null);
        }}
      />
    </div>
  );
}
