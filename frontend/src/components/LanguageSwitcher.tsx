"use client";

import { useTranslation } from '@/lib/i18n';
import { cn } from '@/lib/utils';

export default function LanguageSwitcher() {
  const { language, setLanguage } = useTranslation();

  return (
    <div className="flex gap-1 rounded-full border border-slate-300 bg-white p-0.5 shadow-xs">
      <button
        type="button"
        onClick={() => setLanguage('zh')}
        aria-label="切换为中文"
        aria-pressed={language === 'zh'}
        className={cn(
          "min-h-11 min-w-11 rounded-full px-2 text-xs font-semibold",
          language === 'zh'
            ? "bg-slate-950 text-white"
            : "text-slate-700 hover:bg-slate-100 hover:text-slate-950"
        )}
      >
        中
      </button>
      <button
        type="button"
        onClick={() => setLanguage('en')}
        aria-label="Switch to English"
        aria-pressed={language === 'en'}
        className={cn(
          "min-h-11 min-w-11 rounded-full px-2 text-xs font-semibold",
          language === 'en'
            ? "bg-slate-950 text-white"
            : "text-slate-700 hover:bg-slate-100 hover:text-slate-950"
        )}
      >
        EN
      </button>
    </div>
  );
}
