"use client";

import { useState, useRef, useEffect, type ReactNode } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Bot, User, Send, Loader2, Sparkles, Trash2 } from 'lucide-react';
import { apiFetch } from '@/lib/utils';
import { motion, AnimatePresence } from 'framer-motion';
import { useTranslation } from '@/lib/i18n';
import ConfirmDialog from '@/components/ConfirmDialog';

type Message = {
  id: string;
  role: 'user' | 'agent';
  content: string;
};

function renderInline(text: string) {
  const parts: ReactNode[] = [];
  const pattern = /(\*\*([^*]+)\*\*|\[([^\]]+)\]\((https?:\/\/[^)]+)\)|`([^`]+)`)/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }
    if (match[2]) {
      parts.push(<strong key={match.index} className="font-semibold text-gray-100">{match[2]}</strong>);
    } else if (match[3] && match[4]) {
      parts.push(
        <a key={match.index} href={match[4]} target="_blank" rel="noreferrer" className="text-indigo-300 underline decoration-indigo-400/40 underline-offset-4 hover:text-indigo-200">
          {match[3]}
        </a>
      );
    } else if (match[5]) {
      parts.push(<code key={match.index} className="rounded bg-white/10 px-1.5 py-0.5 text-[0.9em] text-gray-100">{match[5]}</code>);
    }
    lastIndex = pattern.lastIndex;
  }

  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }
  return parts.length ? parts : text;
}

function MarkdownMessage({ content }: { content: string }) {
  const lines = content.split('\n');
  const nodes: ReactNode[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();

    if (!trimmed) {
      i += 1;
      continue;
    }

    if (trimmed.startsWith('|')) {
      const tableLines: string[] = [];
      while (i < lines.length && lines[i].trim().startsWith('|')) {
        tableLines.push(lines[i].trim());
        i += 1;
      }
      const rows = tableLines
        .filter(row => !/^\|?\s*:?-{3,}/.test(row.replace(/\|/g, '').trim()))
        .map(row => row.split('|').slice(1, -1).map(cell => cell.trim()));
      const [head, ...body] = rows;
      if (head?.length) {
        nodes.push(
          <div key={`table-${i}`} className="my-3 overflow-x-auto rounded-md border border-white/10">
            <table className="min-w-full divide-y divide-white/10 text-left text-sm">
              <thead className="bg-white/5 text-xs uppercase text-gray-400">
                <tr>
                  {head.map((cell, idx) => <th key={idx} className="px-3 py-2 font-medium">{renderInline(cell)}</th>)}
                </tr>
              </thead>
              <tbody className="divide-y divide-white/10">
                {body.map((row, rowIdx) => (
                  <tr key={rowIdx} className="align-top">
                    {row.map((cell, cellIdx) => <td key={cellIdx} className="px-3 py-2 text-gray-200">{renderInline(cell)}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      }
      continue;
    }

    if (trimmed === '---') {
      nodes.push(<hr key={`hr-${i}`} className="my-3 border-white/10" />);
      i += 1;
      continue;
    }

    if (trimmed.startsWith('### ')) {
      nodes.push(<h3 key={`h3-${i}`} className="mt-4 text-base font-semibold text-gray-100">{renderInline(trimmed.slice(4))}</h3>);
      i += 1;
      continue;
    }

    if (trimmed.startsWith('## ')) {
      nodes.push(<h2 key={`h2-${i}`} className="mt-1 text-lg font-semibold text-white">{renderInline(trimmed.slice(3))}</h2>);
      i += 1;
      continue;
    }

    if (/^[-*]\s+/.test(trimmed)) {
      const items: string[] = [];
      while (i < lines.length && /^[-*]\s+/.test(lines[i].trim())) {
        items.push(lines[i].trim().replace(/^[-*]\s+/, ''));
        i += 1;
      }
      nodes.push(
        <ul key={`ul-${i}`} className="ml-4 list-disc space-y-1 text-gray-200">
          {items.map((item, idx) => <li key={idx}>{renderInline(item)}</li>)}
        </ul>
      );
      continue;
    }

    nodes.push(<p key={`p-${i}`} className="text-gray-200">{renderInline(line)}</p>);
    i += 1;
  }

  return <div className="space-y-2">{nodes}</div>;
}

export default function AgentPage() {
  const { t } = useTranslation();
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Clear chat ConfirmDialog state
  const [clearDialogOpen, setClearDialogOpen] = useState(false);

  // Auto-scroll to bottom
  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, isLoading]);

  const handleSend = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!input.trim() || isLoading) return;

    const userMessage: Message = { id: Date.now().toString(), role: 'user', content: input.trim() };
    setMessages(prev => [...prev, userMessage]);
    setInput('');
    setIsLoading(true);

    try {
      // Build history for API
      const history = messages.map(m => ({
        role: m.role === 'agent' ? 'assistant' : 'user',
        content: m.content
      }));
      history.push({ role: 'user', content: userMessage.content });

      const res = await apiFetch('/api/agent/chat', {
        method: 'POST',
        body: JSON.stringify({ messages: history })
      });

      if (res.ok) {
        const data = await res.json();
        const agentMessage: Message = {
          id: (Date.now() + 1).toString(),
          role: 'agent',
          content: data.reply
        };
        setMessages(prev => [...prev, agentMessage]);
      } else {
        throw new Error("Failed to get response");
      }
    } catch (error) {
      console.error(error);
      const errorMessage: Message = {
        id: (Date.now() + 1).toString(),
        role: 'agent',
        content: t('I encountered a network error while trying to reach my servers. Please try again.')
      };
      setMessages(prev => [...prev, errorMessage]);
    } finally {
      setIsLoading(false);
    }
  };

  const clearChat = () => {
    setMessages([]);
    setClearDialogOpen(false);
  };

  return (
    <div className="mx-auto flex h-[calc(100vh-7rem)] max-w-5xl flex-col">
      <div className="mb-5 flex shrink-0 flex-col gap-4 sm:flex-row sm:justify-between sm:items-end">
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-indigo-600">{t('Assistant')}</p>
          <h1 className="text-2xl font-semibold tracking-tight text-white sm:text-3xl flex items-center gap-2">
            <Bot className="w-7 h-7 text-indigo-500" /> {t('AI Copilot')}
          </h1>
          <p className="mt-2 text-sm text-gray-400">{t('Live-search companies, enrich contacts, draft outreach, and inspect your pipeline.')}</p>
        </div>
        {messages.length > 0 && (
          <Button onClick={() => setClearDialogOpen(true)} variant="outline" size="sm" className="gap-2 bg-transparent text-gray-400 border-white/10 hover:text-red-400 hover:border-red-400/50">
            <Trash2 className="w-4 h-4" /> {t('Clear Chat')}
          </Button>
        )}
      </div>

      <div className="flex-1 glass-panel rounded-lg border border-white/10 flex flex-col overflow-hidden relative">
        {/* Chat Area */}
        <div className="flex-1 overflow-y-auto p-4 sm:p-6 space-y-6 z-10 scrollbar-thin scrollbar-thumb-white/10 scrollbar-track-transparent">
          {messages.length === 0 ? (
            <div className="h-full flex flex-col items-center justify-center text-center text-gray-400 space-y-4">
              <div className="w-14 h-14 rounded-lg bg-indigo-50 border border-indigo-100 flex items-center justify-center">
                <Sparkles className="w-7 h-7 text-indigo-500" />
              </div>
              <div>
                <h3 className="text-lg font-medium text-gray-200 mb-1">{t('How can I help you grow today?')}</h3>
                <p className="text-sm max-w-sm mx-auto">{t('Ask me to draft a highly personalized cold email, suggest target titles, or optimize your workflow configuration.')}</p>
              </div>
              <div className="grid grid-cols-1 gap-3 mt-6 w-full max-w-xl sm:grid-cols-2">
                <button onClick={() => setInput(t('帮我找10个欧洲padel销售公司，使用真实搜索'))} className="p-4 rounded-lg bg-white/5 border border-white/5 hover:bg-white/10 text-left text-sm transition-colors">
                  &quot;{t('帮我找10个欧洲padel销售公司，使用真实搜索')}&quot;
                </button>
                <button onClick={() => setInput(t('找 castonsports.com 的采购或负责人邮箱'))} className="p-4 rounded-lg bg-white/5 border border-white/5 hover:bg-white/10 text-left text-sm transition-colors">
                  &quot;{t('找 castonsports.com 的采购或负责人邮箱')}&quot;
                </button>
              </div>
            </div>
          ) : (
            <AnimatePresence initial={false}>
              {messages.map((msg) => (
                <motion.div
                  key={msg.id}
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
                >
                  <div className={`flex gap-3 max-w-[92%] sm:max-w-[85%] ${msg.role === 'user' ? 'flex-row-reverse' : 'flex-row'}`}>
                    <div className="shrink-0 mt-1">
                      {msg.role === 'user' ? (
                        <div className="w-8 h-8 rounded-full bg-indigo-600 flex items-center justify-center shadow-sm">
                          <User className="w-4 h-4 text-white" />
                        </div>
                      ) : (
                        <div className="w-8 h-8 rounded-full bg-[#1e1e24] border border-white/10 flex items-center justify-center shadow-sm">
                          <Bot className="w-4 h-4 text-indigo-500" />
                        </div>
                      )}
                    </div>
                    <div
                      className={`px-5 py-3.5 rounded-lg text-[15px] leading-relaxed shadow-sm ${
                        msg.role === 'user'
                          ? 'bg-indigo-600 text-white rounded-tr-sm'
                          : 'bg-[#1a1a1e] border border-white/5 text-gray-200 rounded-tl-sm'
                      }`}
                    >
                      <MarkdownMessage content={msg.content} />
                    </div>
                  </div>
                </motion.div>
              ))}
              {isLoading && (
                <motion.div
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="flex justify-start"
                >
                  <div className="flex gap-3 max-w-[85%] flex-row">
                    <div className="shrink-0 mt-1">
                      <div className="w-8 h-8 rounded-full bg-[#1e1e24] border border-white/10 flex items-center justify-center shadow-sm">
                        <Bot className="w-4 h-4 text-indigo-500" />
                      </div>
                    </div>
                    <div className="px-5 py-4 rounded-lg bg-[#1a1a1e] border border-white/5 text-gray-200 rounded-tl-sm flex items-center gap-2">
                      <div className="w-2 h-2 rounded-full bg-indigo-500 animate-bounce" style={{ animationDelay: '0ms' }} />
                      <div className="w-2 h-2 rounded-full bg-indigo-500 animate-bounce" style={{ animationDelay: '150ms' }} />
                      <div className="w-2 h-2 rounded-full bg-indigo-500 animate-bounce" style={{ animationDelay: '300ms' }} />
                    </div>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          )}
          <div ref={messagesEndRef} />
        </div>

        {/* Input Area */}
        <div className="p-4 bg-black/40 border-t border-white/10 backdrop-blur-md z-10">
          <form onSubmit={handleSend} className="relative flex items-center">
            <Input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={t('Ask Copilot anything...')}
              disabled={isLoading}
              className="w-full pl-5 pr-14 py-6 bg-[#1a1a1e] border-white/10 text-gray-100 placeholder:text-gray-500 rounded-lg focus-visible:ring-indigo-500/50 text-[15px]"
            />
            <Button
              type="submit"
              size="icon"
              disabled={!input.trim() || isLoading}
              className="absolute right-2 top-1/2 -translate-y-1/2 w-10 h-10 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg disabled:opacity-50"
            >
              {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4 ml-0.5" />}
            </Button>
          </form>
          <div className="text-center mt-3">
            <span className="text-[11px] text-gray-500">{t('AI Copilot can make mistakes. Please verify important information.')}</span>
          </div>
        </div>
      </div>

      <ConfirmDialog
        open={clearDialogOpen}
        title={t('Clear Chat')}
        message={t('Clear chat history?')}
        variant="default"
        onConfirm={clearChat}
        onCancel={() => setClearDialogOpen(false)}
      />
    </div>
  );
}
