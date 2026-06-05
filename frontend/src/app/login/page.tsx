"use client";

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { Button } from '@/components/ui/button';
import { Bot, Loader2, ArrowRight } from 'lucide-react';
import { apiUrl, getErrorMessage } from '@/lib/utils';
import { useTranslation } from '@/lib/i18n';
import LanguageSwitcher from '@/components/LanguageSwitcher';

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState('');
  const { t } = useTranslation();

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsLoading(true);
    setError('');

    try {
      const res = await fetch(apiUrl('/api/auth/login'), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ username, password }),
      });

      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.detail || 'Login failed');
      }

      const data = await res.json();
      
      // Store token and user info
      if (typeof window !== 'undefined') {
        localStorage.setItem('auth_token', data.token);
        localStorage.setItem('auth_user', JSON.stringify(data.user));
      }

      // Redirect to dashboard
      router.push('/dashboard');
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Invalid username or password'));
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-slate-950 p-6">
      <div className="absolute right-6 top-6 z-50">
        <LanguageSwitcher />
      </div>

      <div className="absolute inset-x-0 bottom-0 top-20 opacity-35" aria-hidden="true">
        <div className="mx-auto grid h-full max-w-5xl grid-cols-3 gap-4 px-6">
          <div className="rounded-lg border border-white/10 bg-white/[0.04]" />
          <div className="rounded-lg border border-white/10 bg-white/[0.06]" />
          <div className="rounded-lg border border-white/10 bg-white/[0.04]" />
        </div>
      </div>
      <div className="relative z-10 w-full max-w-md">
        <div className="relative overflow-hidden rounded-lg border border-slate-200 bg-white p-8 text-slate-950 shadow-2xl shadow-black/20 md:p-10">
          <div className="flex flex-col items-center mb-10">
            <div className="mb-6 flex h-12 w-12 items-center justify-center rounded-lg bg-slate-950 shadow-sm">
              <Bot className="w-7 h-7 text-white" />
            </div>
            <h1 className="mb-2 text-3xl font-semibold text-slate-950">{t('Welcome Back')}</h1>
            <p className="text-center text-slate-500">{t('Sign in description')}</p>
          </div>

          <form onSubmit={handleLogin} className="space-y-6">
            <div>
              <label className="mb-2 block text-sm font-medium text-slate-700">{t('Username')}</label>
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="h-12 w-full rounded-lg border border-slate-200 bg-slate-50 px-4 text-slate-950 transition-all placeholder:text-slate-400 focus:border-indigo-400 focus:bg-white focus:outline-none focus:ring-4 focus:ring-indigo-500/10"
                placeholder={t('Username')}
                required
              />
            </div>
            <div>
              <label className="mb-2 block text-sm font-medium text-slate-700">{t('Password')}</label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="h-12 w-full rounded-lg border border-slate-200 bg-slate-50 px-4 text-slate-950 transition-all placeholder:text-slate-400 focus:border-indigo-400 focus:bg-white focus:outline-none focus:ring-4 focus:ring-indigo-500/10"
                placeholder="••••••••"
                required
              />
            </div>

            {error && (
              <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-sm">
                {error}
              </div>
            )}

            <Button 
              type="submit" 
              disabled={isLoading}
              className="mt-8 h-12 w-full bg-slate-950 text-base text-white hover:bg-slate-800"
            >
              {isLoading ? (
                <Loader2 className="w-5 h-5 animate-spin" />
              ) : (
                <span className="flex items-center gap-2">{t('Sign In')} <ArrowRight className="w-5 h-5" /></span>
              )}
            </Button>
          </form>

          <div className="mt-8 text-center text-sm text-slate-500">
            {t('No account')}{' '}
            <span className="font-medium text-slate-700">sales@autoleadgen.com</span>
          </div>
        </div>
      </div>
    </div>
  );
}
