"use client";

import { AlertTriangle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useTranslation } from '@/lib/i18n';

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  variant?: 'danger' | 'default';
  onConfirm: () => void;
  onCancel: () => void;
}

export default function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel,
  cancelLabel,
  variant = 'danger',
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const { t } = useTranslation();

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onCancel}
      />

      {/* Dialog */}
      <div className="relative z-10 w-full max-w-md mx-4 bg-slate-900 border border-slate-700/50 rounded-xl shadow-2xl p-6 animate-in">
        <div className="flex items-start gap-4">
          <div className={`p-2.5 rounded-full shrink-0 ${variant === 'danger' ? 'bg-rose-500/10' : 'bg-amber-500/10'}`}>
            <AlertTriangle className={`w-5 h-5 ${variant === 'danger' ? 'text-rose-400' : 'text-amber-400'}`} />
          </div>
          <div className="flex-1 min-w-0">
            <h3 className="text-lg font-semibold text-white mb-1">{title}</h3>
            <p className="text-sm text-gray-400">{message}</p>
          </div>
        </div>

        <div className="flex justify-end gap-3 mt-6">
          <Button
            variant="glass"
            onClick={onCancel}
            className="text-gray-300 hover:text-white"
          >
            {cancelLabel || t('Cancel')}
          </Button>
          <Button
            onClick={onConfirm}
            className={variant === 'danger'
              ? 'bg-rose-600 hover:bg-rose-700 text-white'
              : 'bg-indigo-600 hover:bg-indigo-700 text-white'
            }
          >
            {confirmLabel || t('Yes, delete')}
          </Button>
        </div>
      </div>
    </div>
  );
}
