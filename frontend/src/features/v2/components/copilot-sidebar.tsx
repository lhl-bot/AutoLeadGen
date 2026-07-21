'use client';

import { useEffect, useRef, useState } from 'react';
import { Bot, Check, ChevronRight, Search, Send, Sparkles, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import type { ActionPreview } from '../types';

const previews: Record<string, ActionPreview> = {
  search: {
    id: 'search',
    title: '搜索目标公司',
    target: '当前页面上下文',
    effects: ['将调用搜索或补全 Provider', '可能产生供应商原生计费单位'],
    risks: ['当前本地阶段只允许 fake connector', '结果不会自动加入 Campaign'],
  },
  bulk: {
    id: 'bulk',
    title: '批量修改受众',
    target: '选中的 Company / Contact',
    effects: ['将创建变更预览', '确认后才允许写入 V2'],
    risks: ['可能改变 Campaign readiness', 'Consent 与硬暂停不可覆盖'],
  },
  send: {
    id: 'send',
    title: '请求发送',
    target: '当前 Campaign Revision',
    effects: ['将创建带 Idempotency-Key 的异步命令', '由 worker 再次执行全部硬门槛'],
    risks: ['本地强制 fake connector', '未知 Provider 结果禁止自动重发'],
  },
};

export default function CopilotSidebar() {
  const [open, setOpen] = useState(false);
  const [preview, setPreview] = useState<ActionPreview | null>(null);
  const [notice, setNotice] = useState('Copilot 默认为只读，只解释当前页面中的 V2 数据。');
  const triggerRef = useRef<HTMLButtonElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const wasOpenRef = useRef(false);

  useEffect(() => {
    if (!open) {
      if (wasOpenRef.current) {
        triggerRef.current?.focus();
        wasOpenRef.current = false;
      }
      return;
    }

    wasOpenRef.current = true;
    closeButtonRef.current?.focus();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      setOpen(false);
    };
    document.addEventListener('keydown', closeOnEscape);
    return () => document.removeEventListener('keydown', closeOnEscape);
  }, [open]);

  const acknowledge = () => {
    setNotice(`已确认“${preview?.title}”的影响预览。本地隔离阶段未执行任何外部调用或写操作。`);
    setPreview(null);
  };

  return (
    <>
      <Button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen(true)}
        className="fixed bottom-5 right-5 z-30 min-h-11 bg-slate-950 text-white shadow-lg hover:bg-slate-800"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls="copilot-dialog"
      >
        <Bot className="mr-2 h-4 w-4" />Copilot
      </Button>
      {open ? (
        <div className="fixed inset-0 z-50 flex justify-end" role="presentation">
          <button type="button" tabIndex={-1} aria-label="关闭 Copilot" className="absolute inset-0 bg-slate-950/30" onClick={() => setOpen(false)} />
          <aside id="copilot-dialog" role="dialog" aria-modal="true" aria-labelledby="copilot-title" className="relative flex h-full w-full max-w-md flex-col border-l border-slate-200 bg-white shadow-2xl">
            <header className="flex items-start justify-between border-b border-slate-200 p-5">
              <div>
                <p className="text-xs font-semibold uppercase tracking-[0.14em] text-indigo-700">只读上下文助手</p>
                <h2 id="copilot-title" className="mt-1 text-lg font-semibold text-slate-950">Copilot</h2>
              </div>
              <Button ref={closeButtonRef} variant="ghost" size="icon" onClick={() => setOpen(false)} aria-label="关闭 Copilot" className="min-h-11 min-w-11"><X className="h-5 w-5" /></Button>
            </header>
            <div className="flex-1 space-y-5 overflow-y-auto p-5">
              <div role="status" className="rounded-lg border border-indigo-200 bg-indigo-50 p-4 text-sm leading-6 text-indigo-950">
                <Sparkles className="mb-2 h-4 w-4" />{notice}
              </div>
              <section aria-labelledby="copilot-actions">
                <h3 id="copilot-actions" className="text-sm font-semibold text-slate-950">需要预览与确认的动作</h3>
                <div className="mt-3 space-y-2">
                  {[
                    ['search', '搜索 / 付费补全', Search],
                    ['bulk', '批量修改', ChevronRight],
                    ['send', '发送请求', Send],
                  ].map(([key, label, Icon]) => (
                    <button key={String(key)} type="button" onClick={() => setPreview(previews[String(key)])} className="flex min-h-11 w-full items-center justify-between rounded-lg border border-slate-200 px-3 py-2 text-left text-sm font-medium text-slate-800 hover:border-indigo-300 hover:bg-indigo-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600">
                      <span className="flex items-center gap-2"><Icon className="h-4 w-4 text-slate-500" />{String(label)}</span>
                      <span className="text-xs text-slate-500">先预览</span>
                    </button>
                  ))}
                </div>
              </section>
              {preview ? (
                <section aria-labelledby="preview-title" className="rounded-lg border border-amber-300 bg-amber-50 p-4">
                  <p className="text-xs font-semibold uppercase tracking-[0.12em] text-amber-800">影响预览</p>
                  <h3 id="preview-title" className="mt-1 font-semibold text-amber-950">{preview.title}</h3>
                  <p className="mt-1 text-xs text-amber-900">对象：{preview.target}</p>
                  <h4 className="mt-4 text-xs font-semibold text-amber-950">将发生</h4>
                  <ul className="mt-1 list-disc space-y-1 pl-5 text-xs leading-5 text-amber-950">{preview.effects.map(effect => <li key={effect}>{effect}</li>)}</ul>
                  <h4 className="mt-3 text-xs font-semibold text-amber-950">限制与风险</h4>
                  <ul className="mt-1 list-disc space-y-1 pl-5 text-xs leading-5 text-amber-950">{preview.risks.map(risk => <li key={risk}>{risk}</li>)}</ul>
                  <div className="mt-4 flex gap-2">
                    <Button type="button" onClick={acknowledge} className="min-h-11 bg-slate-950 text-white"><Check className="mr-2 h-4 w-4" />确认预览</Button>
                    <Button type="button" variant="outline" onClick={() => setPreview(null)} className="min-h-11 bg-white">取消</Button>
                  </div>
                </section>
              ) : null}
            </div>
          </aside>
        </div>
      ) : null}
    </>
  );
}
