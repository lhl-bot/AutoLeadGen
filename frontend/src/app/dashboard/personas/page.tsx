"use client";

import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Users, Plus, RefreshCw, Trash2, Target } from 'lucide-react';
import { apiFetch } from '@/lib/utils';
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

export default function PersonasPage() {
  const [personas, setPersonas] = useState<CustomerPersona[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  // Form State
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [isCreating, setIsCreating] = useState(false);
  const [formData, setFormData] = useState<PersonaForm>(emptyPersonaForm);

  const fetchPersonas = async () => {
    setIsLoading(true);
    try {
      const res = await apiFetch('/api/personas/');
      if (res.ok) {
        const data = await res.json();
        setPersonas(data);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchPersonas();
  }, []);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsCreating(true);
    try {
      const res = await apiFetch('/api/personas/', {
        method: 'POST',
        body: JSON.stringify(formData)
      });
      if (res.ok) {
        setIsCreateOpen(false);
        fetchPersonas();
        setFormData(emptyPersonaForm);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setIsCreating(false);
    }
  };

  const deletePersona = async (id: number) => {
    if(!confirm('确定要删除这个画像吗？')) return;
    try {
      await apiFetch(`/api/personas/${id}`, { method: 'DELETE' });
      fetchPersonas();
    } catch(e) {
      console.error(e);
    }
  };

  return (
    <div>
      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:justify-between sm:items-end">
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-indigo-600">Workspace</p>
          <h1 className="text-2xl font-semibold tracking-tight text-white sm:text-3xl">Customer Personas</h1>
          <p className="mt-2 text-sm text-gray-400">Define your ideal customer profiles for better AI personalization.</p>
        </div>
        <div className="flex flex-wrap gap-3">
          <Button onClick={fetchPersonas} variant="outline" className="gap-2 bg-transparent text-slate-700 border-white/20">
            <RefreshCw className="w-4 h-4" /> Refresh
          </Button>

          <Dialog open={isCreateOpen} onOpenChange={setIsCreateOpen}>
            <DialogTrigger asChild>
              <Button className="gap-2 bg-indigo-600 hover:bg-indigo-700 text-white border-0">
                <Plus className="w-4 h-4" /> New Persona
              </Button>
            </DialogTrigger>
            <DialogContent className="sm:max-w-[600px] max-h-[90vh] overflow-y-auto">
              <DialogHeader>
                <DialogTitle>Create Customer Persona</DialogTitle>
              </DialogHeader>
              <form onSubmit={handleCreate} className="space-y-4 mt-4">
                <div className="space-y-2">
                  <Label>Persona Name *</Label>
                  <Input required value={formData.name} onChange={e => setFormData({...formData, name: e.target.value})} placeholder="e.g. European Padel Retailers" />
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label>Target Industry</Label>
                    <Input value={formData.target_industry} onChange={e => setFormData({...formData, target_industry: e.target.value})} placeholder="e.g. Sports Equipment" />
                  </div>
                  <div className="space-y-2">
                    <Label>Target Countries</Label>
                    <Input value={formData.target_countries} onChange={e => setFormData({...formData, target_countries: e.target.value})} placeholder="e.g. Spain, Italy" />
                  </div>
                </div>

                <div className="space-y-2">
                  <Label>Target Job Titles</Label>
                  <Input value={formData.target_roles} onChange={e => setFormData({...formData, target_roles: e.target.value})} placeholder="e.g. CEO, Founder, Buyer" />
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label>Buyer Types</Label>
                    <Input value={formData.customer_types} onChange={e => setFormData({...formData, customer_types: e.target.value})} placeholder="e.g. distributor, agent, brand" />
                  </div>
                  <div className="space-y-2">
                    <Label>Product Categories</Label>
                    <Input value={formData.product_categories} onChange={e => setFormData({...formData, product_categories: e.target.value})} placeholder="e.g. skiwear, functional apparel" />
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label>Search Keywords</Label>
                    <Input value={formData.target_keywords} onChange={e => setFormData({...formData, target_keywords: e.target.value})} placeholder="e.g. padel store" />
                  </div>
                  <div className="space-y-2">
                    <Label>Negative Keywords</Label>
                    <Input value={formData.negative_keywords} onChange={e => setFormData({...formData, negative_keywords: e.target.value})} placeholder="e.g. manufacturer, factory" />
                  </div>
                </div>

                <div className="space-y-2">
                  <Label>Evidence Sources</Label>
                  <Input value={formData.evidence_sources} onChange={e => setFormData({...formData, evidence_sources: e.target.value})} placeholder="e.g. website, social media, customs data, trade show list" />
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label>Qualification Rules</Label>
                    <Textarea value={formData.qualification_rules} onChange={e => setFormData({...formData, qualification_rules: e.target.value})} className="min-h-[90px]" placeholder="Signals that make a lead a good fit..." />
                  </div>
                  <div className="space-y-2">
                    <Label>Disqualification Rules</Label>
                    <Textarea value={formData.disqualification_rules} onChange={e => setFormData({...formData, disqualification_rules: e.target.value})} className="min-h-[90px]" placeholder="Signals to avoid, e.g. factory, supplier, job board..." />
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label>Positive Examples</Label>
                    <Textarea value={formData.positive_examples} onChange={e => setFormData({...formData, positive_examples: e.target.value})} className="min-h-[80px]" placeholder="Known customers or ideal company examples..." />
                  </div>
                  <div className="space-y-2">
                    <Label>Negative Examples</Label>
                    <Textarea value={formData.negative_examples} onChange={e => setFormData({...formData, negative_examples: e.target.value})} className="min-h-[80px]" placeholder="Companies that should be filtered out..." />
                  </div>
                </div>

                <div className="space-y-2">
                  <Label>Culture / Localization Notes</Label>
                  <Textarea value={formData.cultural_notes} onChange={e => setFormData({...formData, cultural_notes: e.target.value})} className="min-h-[80px]" placeholder="e.g. Europe values quality/certifications; Southeast Asia is more price-sensitive." />
                </div>

                <div className="space-y-2">
                  <Label>AI Email Guidelines</Label>
                  <Textarea value={formData.ai_prompt_template} onChange={e => setFormData({...formData, ai_prompt_template: e.target.value})} className="min-h-[100px]" placeholder="Tell AI how to pitch this persona... e.g. Focus on ROI and quick shipping times." />
                </div>

                <div className="pt-4 flex justify-end">
                  <Button type="submit" disabled={isCreating} className="bg-indigo-600 hover:bg-indigo-700 text-white">
                    {isCreating ? 'Saving...' : 'Save Persona'}
                  </Button>
                </div>
              </form>
            </DialogContent>
          </Dialog>
        </div>
      </div>

      {isLoading ? (
        <div className="py-20 text-center text-gray-500">Loading personas...</div>
      ) : personas.length === 0 ? (
        <div className="glass-panel p-12 text-center text-gray-400 rounded-lg border border-dashed border-white/20">
          <Users className="w-12 h-12 mx-auto mb-4 opacity-50" />
          <p>No personas created yet. Click &quot;New Persona&quot; to get started.</p>
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {personas.map(persona => (
            <div key={persona.id} className="glass-panel p-5 rounded-lg flex flex-col justify-between hover:border-indigo-500/50 hover:shadow-[0_12px_32px_rgba(79,70,229,0.12)] transition-all">
              <div>
                <div className="flex justify-between items-start mb-2">
                  <h3 className="font-bold text-lg text-white">{persona.name}</h3>
                  <button onClick={() => deletePersona(persona.id)} className="text-gray-500 hover:text-red-400 transition-colors">
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
                <div className="text-sm text-gray-400 mb-4 flex items-center gap-2">
                  <Target className="w-4 h-4" /> {persona.target_industry || 'Any Industry'} | {persona.target_countries || 'Global'}
                </div>
                
                <div className="space-y-3 mt-4 pt-4 border-t border-white/10 text-sm">
                  <div>
                    <span className="text-gray-500 block mb-1">Target Roles</span>
                    <span className="text-gray-200">{persona.target_roles || '—'}</span>
                  </div>
                  <div>
                    <span className="text-gray-500 block mb-1">Buyer Types</span>
                    <span className="text-gray-200">{persona.customer_types || '—'}</span>
                  </div>
                  <div>
                    <span className="text-gray-500 block mb-1">Product Categories</span>
                    <span className="text-gray-200">{persona.product_categories || '—'}</span>
                  </div>
                  <div>
                    <span className="text-gray-500 block mb-1">Keywords</span>
                    <span className="text-indigo-600">{persona.target_keywords || '—'}</span>
                  </div>
                  {persona.evidence_sources && (
                    <div>
                      <span className="text-gray-500 block mb-1">Evidence Sources</span>
                      <span className="text-gray-300">{persona.evidence_sources}</span>
                    </div>
                  )}
                  {persona.negative_keywords && (
                    <div>
                      <span className="text-gray-500 block mb-1">Negative Keywords</span>
                      <span className="text-rose-400/80">{persona.negative_keywords}</span>
                    </div>
                  )}
                  {(persona.qualification_rules || persona.disqualification_rules) && (
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <span className="text-gray-500 block mb-1">Good-fit Rules</span>
                        <span className="text-gray-300">{persona.qualification_rules || '—'}</span>
                      </div>
                      <div>
                        <span className="text-gray-500 block mb-1">Filter-out Rules</span>
                        <span className="text-rose-300/80">{persona.disqualification_rules || '—'}</span>
                      </div>
                    </div>
                  )}
                  {persona.cultural_notes && (
                    <div>
                      <span className="text-gray-500 block mb-1">Localization Notes</span>
                      <span className="text-gray-300">{persona.cultural_notes}</span>
                    </div>
                  )}
                  {persona.ai_prompt_template && (
                    <div className="mt-4 p-3 bg-white/5 border-l-2 border-indigo-500 rounded text-gray-300 text-xs">
                      <strong>AI Guidelines:</strong> {persona.ai_prompt_template}
                    </div>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
