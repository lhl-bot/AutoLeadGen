"use client";

import { useEffect } from 'react';
import { useTranslation } from '@/lib/i18n';

export default function DynamicLang() {
  const { language } = useTranslation();

  useEffect(() => {
    document.documentElement.lang = language === 'zh' ? 'zh-CN' : 'en';
  }, [language]);

  useEffect(() => {
    const handleRejection = (event: PromiseRejectionEvent) => {
      const error = event.reason;
      if (error && (error.name === 'AbortError' || error.message?.includes('aborted') || error.message?.includes('AbortError'))) {
        event.preventDefault();
        console.log('Suppressed unhandled AbortError rejection:', error);
      }
    };

    const handleError = (event: ErrorEvent) => {
      const error = event.error;
      if (error && (error.name === 'AbortError' || error.message?.includes('aborted') || error.message?.includes('AbortError'))) {
        event.preventDefault();
        console.log('Suppressed unhandled AbortError error:', error);
      }
    };

    window.addEventListener('unhandledrejection', handleRejection);
    window.addEventListener('error', handleError);

    return () => {
      window.removeEventListener('unhandledrejection', handleRejection);
      window.removeEventListener('error', handleError);
    };
  }, []);

  return null;
}
