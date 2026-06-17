"use client";

import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { MessageSquare, RefreshCw, Reply, Send, UserCircle, Loader2 } from 'lucide-react';
import { cn, apiFetch, formatApiDetail } from '@/lib/utils';
import type { ReplyLead } from '@/lib/types';
import { Badge } from '@/components/ui/badge';
import { useTranslation } from '@/lib/i18n';
import { toast } from 'sonner';

export default function RepliesPage() {
  const [replies, setReplies] = useState<ReplyLead[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [generatingId, setGeneratingId] = useState<number | null>(null);
  const [sendingId, setSendingId] = useState<number | null>(null);
  const [drafts, setDrafts] = useState<Record<number, { intent: string; draft: string; summary: string } | null>>({});
  const [editingDrafts, setEditingDrafts] = useState<Record<number, string>>({});
  const { t } = useTranslation();

  const fetchReplies = async () => {
    setIsLoading(true);
    try {
      const res = await apiFetch('/api/replies/');
      if (res.ok) {
        const data: ReplyLead[] = await res.json();
        // Surface the hottest leads first: handoff-recommended, then most
        // recently active, then best-fit — so reps reply to the best ones first.
        data.sort((a, b) => {
          const handoff = Number(b.handoff_recommended ?? false) - Number(a.handoff_recommended ?? false);
          if (handoff !== 0) return handoff;
          const recency = new Date(b.last_reply_at || 0).getTime() - new Date(a.last_reply_at || 0).getTime();
          if (recency !== 0) return recency;
          return (b.fit_score ?? -1) - (a.fit_score ?? -1);
        });
        setReplies(data);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchReplies();
  }, []);

  const generateDraft = async (leadId: number) => {
    setGeneratingId(leadId);
    try {
      const res = await apiFetch(`/api/replies/${leadId}/generate-draft`, { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        setDrafts(prev => ({ ...prev, [leadId]: data }));
        if (data.draft) {
          setEditingDrafts(prev => ({ ...prev, [leadId]: data.draft }));
        }
        if (data.intent === 'not_interested') {
          toast.info('This lead is not interested — no draft generated.');
        } else {
          toast.success('AI draft generated');
        }
      } else {
        const err = await res.json().catch(() => ({}));
        toast.error(formatApiDetail(err.detail, 'Failed to generate AI response'));
      }
    } catch (e) {
      console.error(e);
      toast.error('Network error generating response');
    } finally {
      setGeneratingId(null);
    }
  };

  const sendDraft = async (leadId: number) => {
    const draft = editingDrafts[leadId];
    if (!draft?.trim()) {
      toast.error('Draft is empty');
      return;
    }
    setSendingId(leadId);
    try {
      const res = await apiFetch(`/api/replies/${leadId}/send`, {
        method: 'POST',
        body: JSON.stringify({ draft }),
      });
      if (res.ok) {
        toast.success('Reply sent');
        setDrafts(prev => ({ ...prev, [leadId]: null }));
        setEditingDrafts(prev => {
          const next = { ...prev };
          delete next[leadId];
          return next;
        });
        fetchReplies();
      } else {
        const err = await res.json().catch(() => ({}));
        toast.error(formatApiDetail(err.detail, 'Failed to send reply'));
      }
    } catch (e) {
      console.error(e);
      toast.error('Network error sending reply');
    } finally {
      setSendingId(null);
    }
  };

  return (
    <div>
      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:justify-between sm:items-end">
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-indigo-600">{t('Reports')}</p>
          <h1 className="text-2xl font-semibold tracking-tight text-white sm:text-3xl">{t('Client Replies')}</h1>
          <p className="mt-2 text-sm text-gray-400">{t('Track all incoming responses from your leads across channels.')}</p>
        </div>
        <Button onClick={fetchReplies} variant="outline" className="gap-2 bg-transparent text-slate-700 border-white/20">
          <RefreshCw className="w-4 h-4" /> {t('Refresh')}
        </Button>
      </div>

      {isLoading ? (
        <div className="py-20 text-center text-gray-500">{t('Loading replies...')}</div>
      ) : replies.length === 0 ? (
        <div className="glass-panel p-12 text-center text-gray-400 rounded-lg border border-dashed border-white/20">
          <MessageSquare className="w-12 h-12 mx-auto mb-4 opacity-50" />
          <p>{t('No replies received yet. Make sure your workflows are active.')}</p>
        </div>
      ) : (
        <div className="space-y-6">
          {replies.map(reply => {
            const leadName = `${reply.first_name || ''} ${reply.last_name || ''}`.trim()
            const displayName = leadName || reply.email || reply.company_name || reply.domain || t('Unknown lead')
            const receivedAt = reply.last_reply_at ? new Date(reply.last_reply_at).toLocaleString() : t('Unknown time')
            const content = reply.reply_snippet || t('No reply snippet captured yet.')
            const draftData = drafts[reply.id]
            const isGenerating = generatingId === reply.id

            return (
            <div key={reply.id} className="glass-panel p-5 rounded-lg">
              <div className="flex gap-4">
                <div className="w-12 h-12 rounded-full bg-indigo-500/20 flex items-center justify-center shrink-0">
                  <UserCircle className="w-6 h-6 text-indigo-500" />
                </div>
                <div className="flex-1">
                  <div className="flex justify-between items-start mb-1">
                    <div>
                      <h3 className="font-bold text-white text-lg">{displayName}</h3>
                      <div className="text-sm text-gray-400 flex items-center gap-2">
                        <span>{reply.company_name || reply.domain || t('Unknown company')}</span>
                        <span className="text-gray-600">/</span>
                        <span>{t('Workflow')} {reply.workflow_id || '—'}</span>
                        {reply.status && (
                          <span className={cn(
                            "px-2 py-0.5 rounded text-xs font-semibold uppercase",
                            reply.status === 'replied' ? "bg-emerald-500/20 text-emerald-500" :
                            "bg-gray-500/20 text-gray-400"
                          )}>
                            {reply.status}
                          </span>
                        )}
                        {reply.fit_grade && (
                          <Badge variant="secondary" className="bg-sky-500/10 text-sky-400 border-sky-500/20">
                            Fit {reply.fit_grade} · {reply.fit_score ?? '—'}
                          </Badge>
                        )}
                        {reply.handoff_recommended && (
                          <Badge variant="secondary" className="bg-amber-500/10 text-amber-400 border-amber-500/20">
                            {t('Handoff')}
                          </Badge>
                        )}
                      </div>
                    </div>
                    <div className="text-xs text-gray-500">
                      {receivedAt}
                    </div>
                  </div>

                  <div className="mt-4 bg-black/40 p-4 rounded-lg text-gray-300 leading-relaxed border border-white/5 whitespace-pre-wrap">
                    {content}
                  </div>

                  {/* AI Generated Draft */}
                  {draftData && draftData.intent === 'not_interested' && (
                    <div className="mt-4 bg-rose-500/5 p-4 rounded-lg border border-rose-500/20">
                      <Badge className="bg-rose-500/20 text-rose-400 border-rose-500/30 mb-2">
                        not_interested
                      </Badge>
                      <p className="text-sm text-gray-400">{draftData.summary}</p>
                    </div>
                  )}

                  {(draftData?.draft || editingDrafts[reply.id]) && draftData?.intent !== 'not_interested' && (
                    <div className="mt-4 bg-indigo-500/5 p-4 rounded-lg border border-indigo-500/20">
                      <div className="flex items-center gap-2 mb-2">
                        {draftData && (
                          <>
                            <Badge className="bg-indigo-500/20 text-indigo-400 border-indigo-500/30">
                              {draftData.intent}
                            </Badge>
                            <span className="text-xs text-gray-500">{draftData.summary}</span>
                          </>
                        )}
                      </div>
                      <textarea
                        className="w-full min-h-[160px] bg-black/40 text-gray-200 text-sm leading-relaxed rounded-lg border border-white/10 p-4 focus:border-indigo-500/50 focus:outline-none resize-y"
                        value={editingDrafts[reply.id] || draftData?.draft || ''}
                        onChange={(e) => setEditingDrafts(prev => ({ ...prev, [reply.id]: e.target.value }))}
                      />
                    </div>
                  )}

                  <div className="mt-4 flex gap-3">
                    <Button
                      variant="glass"
                      size="sm"
                      className="gap-2 text-indigo-600 border-indigo-500/30 hover:bg-indigo-500/20"
                      onClick={() => generateDraft(reply.id)}
                      disabled={isGenerating || sendingId === reply.id}
                    >
                      {isGenerating ? (
                        <><Loader2 className="w-4 h-4 animate-spin" /> Generating...</>
                      ) : (
                        <><Reply className="w-4 h-4" /> {t('Generate AI Response')}</>
                      )}
                    </Button>
                    {editingDrafts[reply.id] && (
                      <Button
                        variant="glass"
                        size="sm"
                        className="gap-2 text-emerald-600 border-emerald-500/30 hover:bg-emerald-500/20"
                        onClick={() => sendDraft(reply.id)}
                        disabled={isGenerating || sendingId === reply.id}
                      >
                        {sendingId === reply.id ? (
                          <><Loader2 className="w-4 h-4 animate-spin" /> Sending...</>
                        ) : (
                          <><Send className="w-4 h-4" /> {t('Send Reply')}</>
                        )}
                      </Button>
                    )}
                  </div>
                </div>
              </div>
            </div>
            )
          })}
        </div>
      )}
    </div>
  );
}
