"use client";

import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from '@/lib/i18n';
import { apiFetch } from '@/lib/utils';
import { toast } from 'sonner';
import { KanbanSquare, RefreshCw, Building2, Mail as MailIcon } from 'lucide-react';
import type { ClientPool } from '@/lib/types';

interface BoardCard {
  id: number;
  company_name?: string | null;
  domain: string;
  first_name?: string | null;
  last_name?: string | null;
  email?: string | null;
  job_title?: string | null;
  status: string;
  fit_score?: number | null;
  fit_grade?: string | null;
  sales_stage: string;
}

interface BoardData {
  stages: string[];
  columns: Record<string, BoardCard[]>;
  totals: Record<string, number>;
  total_leads: number;
}

const STAGE_META: Record<string, { en: string; zh: string; color: string; dot: string }> = {
  new: { en: 'New', zh: '新建', color: 'border-slate-300', dot: 'bg-slate-400' },
  contacted: { en: 'Contacted', zh: '已联系', color: 'border-blue-300', dot: 'bg-blue-400' },
  interested: { en: 'Interested', zh: '有意向', color: 'border-indigo-300', dot: 'bg-indigo-500' },
  quoting: { en: 'Quoting', zh: '报价中', color: 'border-amber-300', dot: 'bg-amber-500' },
  won: { en: 'Won', zh: '成交', color: 'border-emerald-300', dot: 'bg-emerald-500' },
  lost: { en: 'Lost', zh: '流失', color: 'border-rose-300', dot: 'bg-rose-400' },
};

function statusBadgeClass(status: string): string {
  if (status === 'replied') return 'bg-emerald-100 text-emerald-700';
  if (status === 'sent') return 'bg-blue-100 text-blue-700';
  if (status === 'bounced' || status === 'rejected' || status === 'unsubscribed') return 'bg-rose-100 text-rose-700';
  if (status === 'drafted') return 'bg-indigo-100 text-indigo-700';
  return 'bg-slate-100 text-slate-600';
}

export default function PipelinePage() {
  const { language } = useTranslation();
  const txt = (en: string, zh: string) => (language === 'zh' ? zh : en);
  const [board, setBoard] = useState<BoardData | null>(null);
  const [pools, setPools] = useState<ClientPool[]>([]);
  const [poolFilter, setPoolFilter] = useState<string>('all');
  const [isLoading, setIsLoading] = useState(true);
  const [dragId, setDragId] = useState<number | null>(null);
  const [dragOverStage, setDragOverStage] = useState<string | null>(null);

  const fetchBoard = useCallback(async () => {
    setIsLoading(true);
    try {
      const qs = poolFilter !== 'all' ? `?pool_id=${poolFilter}` : '';
      const res = await apiFetch(`/api/leads/board${qs}`);
      if (res.ok) setBoard(await res.json());
    } catch (e) {
      console.error(e);
    } finally {
      setIsLoading(false);
    }
  }, [poolFilter]);

  useEffect(() => {
    apiFetch('/api/client_pools/')
      .then(async r => { if (r.ok) setPools(await r.json()); })
      .catch(() => {});
  }, []);
  useEffect(() => { fetchBoard(); }, [fetchBoard]);

  const moveCard = async (cardId: number, fromStage: string, toStage: string) => {
    if (fromStage === toStage || !board) return;
    // Optimistic move.
    const card = board.columns[fromStage]?.find(c => c.id === cardId);
    if (!card) return;
    const prev = board;
    setBoard({
      ...board,
      columns: {
        ...board.columns,
        [fromStage]: board.columns[fromStage].filter(c => c.id !== cardId),
        [toStage]: [{ ...card, sales_stage: toStage }, ...(board.columns[toStage] || [])],
      },
      totals: {
        ...board.totals,
        [fromStage]: Math.max(0, (board.totals[fromStage] || 0) - 1),
        [toStage]: (board.totals[toStage] || 0) + 1,
      },
    });
    try {
      const res = await apiFetch(`/api/leads/${cardId}/stage`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ stage: toStage }),
      });
      if (!res.ok) throw new Error('failed');
    } catch (e) {
      console.error(e);
      setBoard(prev); // rollback
      toast.error(txt('Could not move lead.', '移动失败。'));
    }
  };

  const stages = board?.stages || Object.keys(STAGE_META);

  return (
    <div>
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-indigo-600">{txt('Sales', '销售')}</p>
          <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight text-slate-950 sm:text-3xl">
            <KanbanSquare className="h-7 w-7 text-indigo-500" /> {txt('Pipeline', '销售漏斗')}
          </h1>
          <p className="mt-2 text-sm text-slate-500">{txt('Drag leads across stages. Stages are independent of the automation status.', '拖动线索调整阶段。销售阶段与自动化状态相互独立。')}</p>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={poolFilter}
            onChange={e => setPoolFilter(e.target.value)}
            className="h-9 rounded-md border border-slate-300 bg-white px-3 text-sm text-slate-700"
          >
            <option value="all">{txt('All client pools', '全部客户池')}</option>
            {pools.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          <button onClick={fetchBoard} className="rounded-md border border-slate-300 p-2 text-slate-500 hover:bg-slate-50" aria-label={txt('Refresh', '刷新')}>
            <RefreshCw className={`h-4 w-4 ${isLoading ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {isLoading && !board ? (
        <div className="py-20 text-center text-slate-500">{txt('Loading board...', '正在加载看板...')}</div>
      ) : (
        <div className="flex gap-3 overflow-x-auto pb-4">
          {stages.map(stage => {
            const meta = STAGE_META[stage] || { en: stage, zh: stage, color: 'border-slate-300', dot: 'bg-slate-400' };
            const cards = board?.columns[stage] || [];
            const total = board?.totals[stage] ?? cards.length;
            return (
              <div
                key={stage}
                onDragOver={e => { e.preventDefault(); setDragOverStage(stage); }}
                onDragLeave={() => setDragOverStage(s => (s === stage ? null : s))}
                onDrop={e => {
                  e.preventDefault();
                  setDragOverStage(null);
                  const id = Number(e.dataTransfer.getData('text/plain')) || dragId;
                  const card = board && Object.values(board.columns).flat().find(c => c.id === id);
                  if (id && card) moveCard(id, card.sales_stage, stage);
                  setDragId(null);
                }}
                className={`flex min-w-[260px] max-w-[280px] flex-1 flex-col rounded-lg border-t-4 bg-slate-50/70 ${meta.color} ${dragOverStage === stage ? 'ring-2 ring-indigo-300' : ''}`}
              >
                <div className="flex items-center justify-between gap-2 px-3 py-2.5">
                  <div className="flex items-center gap-2">
                    <span className={`h-2 w-2 rounded-full ${meta.dot}`} />
                    <span className="text-sm font-semibold text-slate-800">{txt(meta.en, meta.zh)}</span>
                  </div>
                  <span className="rounded-full bg-white px-2 py-0.5 text-xs font-medium text-slate-500 ring-1 ring-slate-200">{total}</span>
                </div>
                <div className="flex-1 space-y-2 overflow-y-auto px-2 pb-3" style={{ maxHeight: 'calc(100vh - 260px)' }}>
                  {cards.length === 0 ? (
                    <div className="rounded-md border border-dashed border-slate-200 py-8 text-center text-xs text-slate-400">
                      {txt('Drop here', '拖到此处')}
                    </div>
                  ) : cards.map(card => (
                    <div
                      key={card.id}
                      draggable
                      onDragStart={e => { setDragId(card.id); e.dataTransfer.setData('text/plain', String(card.id)); e.dataTransfer.effectAllowed = 'move'; }}
                      onDragEnd={() => { setDragId(null); setDragOverStage(null); }}
                      className={`cursor-grab rounded-md border border-slate-200 bg-white p-2.5 shadow-sm transition-shadow hover:shadow active:cursor-grabbing ${dragId === card.id ? 'opacity-50' : ''}`}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div className="flex min-w-0 items-center gap-1.5">
                          <Building2 className="h-3.5 w-3.5 shrink-0 text-slate-400" />
                          <span className="truncate text-sm font-medium text-slate-900">{card.company_name || card.domain}</span>
                        </div>
                        {card.fit_grade && (
                          <span className="shrink-0 rounded bg-slate-100 px-1.5 text-[10px] font-bold text-slate-600">{card.fit_grade}</span>
                        )}
                      </div>
                      <div className="mt-1 truncate text-xs text-slate-500">
                        {[card.first_name, card.last_name].filter(Boolean).join(' ') || card.job_title || '—'}
                      </div>
                      {card.email && (
                        <div className="mt-1 flex items-center gap-1 truncate text-[11px] text-slate-400">
                          <MailIcon className="h-3 w-3 shrink-0" /> <span className="truncate">{card.email}</span>
                        </div>
                      )}
                      <div className="mt-2">
                        <span className={`inline-block rounded px-1.5 py-0.5 text-[10px] font-medium ${statusBadgeClass(card.status)}`}>{card.status}</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
