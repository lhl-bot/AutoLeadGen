"use client";

import { useTranslation } from '@/lib/i18n';

export default function LanguageSwitcher() {
  const { language, setLanguage } = useTranslation();

  return (
    <div className="flex gap-1 border border-white/10 bg-white/5 rounded-full p-0.5">
      <button
        type="button"
        onClick={() => setLanguage('zh')}
        className={`h-7 px-2 rounded-full text-xs font-semibold transition-colors ${language === 'zh' ? "bg-white text-slate-950" : "text-gray-400 hover:text-white"}`}
      >
        中
      </button>
      <button
        type="button"
        onClick={() => setLanguage('en')}
        className={`h-7 px-2 rounded-full text-xs font-semibold transition-colors ${language === 'en' ? "bg-white text-slate-950" : "text-gray-400 hover:text-white"}`}
      >
        EN
      </button>
    </div>
  );
}
