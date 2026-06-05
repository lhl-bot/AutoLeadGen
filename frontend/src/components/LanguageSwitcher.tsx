"use client";

import { useTranslation } from '@/lib/i18n';
import { cn } from '@/lib/utils';

export default function LanguageSwitcher() {
  const { language, setLanguage } = useTranslation();

  return (
    <div className="flex gap-1 rounded-full border border-slate-200/80 bg-white/80 p-0.5 shadow-xs backdrop-blur dark:border-white/10 dark:bg-white/10">
      <button
        type="button"
        onClick={() => setLanguage('zh')}
        className={cn(
          "h-7 rounded-full px-2 text-xs font-semibold transition-colors",
          language === 'zh'
            ? "bg-slate-950 text-white dark:bg-white dark:text-slate-950"
            : "text-slate-500 hover:text-slate-950 dark:text-slate-300 dark:hover:text-white"
        )}
      >
        中
      </button>
      <button
        type="button"
        onClick={() => setLanguage('en')}
        className={cn(
          "h-7 rounded-full px-2 text-xs font-semibold transition-colors",
          language === 'en'
            ? "bg-slate-950 text-white dark:bg-white dark:text-slate-950"
            : "text-slate-500 hover:text-slate-950 dark:text-slate-300 dark:hover:text-white"
        )}
      >
        EN
      </button>
    </div>
  );
}
