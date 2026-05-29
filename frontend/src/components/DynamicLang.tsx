"use client";

import { useEffect } from 'react';
import { useTranslation } from '@/lib/i18n';

export default function DynamicLang() {
  const { language } = useTranslation();

  useEffect(() => {
    document.documentElement.lang = language === 'zh' ? 'zh-CN' : 'en';
  }, [language]);

  return null;
}
