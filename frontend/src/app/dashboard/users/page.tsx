"use client";

import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import {
  UserCog,
  WalletCards,
  Plus,
  RefreshCw,
  Trash2,
  CheckCircle2,
  XCircle,
  ShieldAlert,
  User,
  Shield
} from 'lucide-react';
import { apiFetch, formatApiDetail } from '@/lib/utils';
import { toast } from 'sonner';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { User as UserType } from '@/lib/types';
import { useTranslation } from '@/lib/i18n';
import ConfirmDialog from '@/components/ConfirmDialog';

interface CreateUserForm {
  username: string;
  password: string;
  display_name: string;
  is_admin: boolean;
  initial_credits: number;
}

const emptyForm: CreateUserForm = {
  username: '',
  password: '',
  display_name: '',
  is_admin: false,
  initial_credits: 100,
};

export default function UsersPage() {
  const { t } = useTranslation();
  const [users, setUsers] = useState<UserType[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [currentUser, setCurrentUser] = useState<UserType | null>(null);

  // Form State
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [isCreating, setIsCreating] = useState(false);
  const [formData, setFormData] = useState<CreateUserForm>(emptyForm);
  const [errorMessage, setErrorMessage] = useState('');
  const [creditDialogOpen, setCreditDialogOpen] = useState(false);
  const [creditUser, setCreditUser] = useState<UserType | null>(null);
  const [creditAmount, setCreditAmount] = useState(100);
  const [creditDescription, setCreditDescription] = useState('');
  const [isAdjustingCredits, setIsAdjustingCredits] = useState(false);

  // Delete Confirm Dialog State
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [deleteUserId, setDeleteUserId] = useState<number | null>(null);

  const fetchUsers = async () => {
    setIsLoading(true);
    try {
      const res = await apiFetch('/api/auth/users');
      if (res.ok) {
        const data = await res.json();
        setUsers(data);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchUsers();

    if (typeof window !== 'undefined') {
      const userStr = window.localStorage.getItem('auth_user');
      if (userStr) {
        try {
          setCurrentUser(JSON.parse(userStr) as UserType);
        } catch (e) {
          console.error(e);
        }
      }
    }
  }, []);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsCreating(true);
    setErrorMessage('');
    try {
      const res = await apiFetch('/api/auth/users', {
        method: 'POST',
        body: JSON.stringify(formData),
      });
      if (res.ok) {
        setIsCreateOpen(false);
        fetchUsers();
        setFormData(emptyForm);
      } else {
        const err = await res.json();
        setErrorMessage(err.detail || t('Operation failed'));
      }
    } catch (e) {
      console.error(e);
      setErrorMessage(t('Network error'));
    } finally {
      setIsCreating(false);
    }
  };

  const toggleActive = async (id: number) => {
    if (currentUser && currentUser.id === id) {
      toast.error(t('Cannot disable yourself'));
      return;
    }
    try {
      const res = await apiFetch(`/api/auth/users/${id}/toggle-active`, {
        method: 'POST',
      });
      if (res.ok) {
        fetchUsers();
        toast.success(t('User toggled'));
      } else {
        const err = await res.json();
        toast.error(err.detail || t('Operation failed'));
      }
    } catch (e) {
      console.error(e);
      toast.error(t('Network error'));
    }
  };

  const handleDeleteClick = (id: number) => {
    if (currentUser && currentUser.id === id) {
      toast.error(t('Cannot delete yourself'));
      return;
    }
    setDeleteUserId(id);
    setDeleteDialogOpen(true);
  };

  const handleDeleteConfirm = async () => {
    if (deleteUserId === null) return;
    try {
      const res = await apiFetch(`/api/auth/users/${deleteUserId}`, {
        method: 'DELETE',
      });
      if (res.ok) {
        fetchUsers();
        toast.success(t('User deleted'));
      } else {
        const err = await res.json();
        toast.error(err.detail || t('Operation failed'));
      }
    } catch (e) {
      console.error(e);
      toast.error(t('Network error'));
    } finally {
      setDeleteDialogOpen(false);
      setDeleteUserId(null);
    }
  };

  const handleCreditClick = (user: UserType) => {
    setCreditUser(user);
    setCreditAmount(100);
    setCreditDescription('');
    setCreditDialogOpen(true);
  };

  const handleCreditSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!creditUser) return;
    if (!creditAmount) {
      toast.error(t('Amount cannot be zero'));
      return;
    }

    setIsAdjustingCredits(true);
    try {
      const res = await apiFetch(`/api/credits/users/${creditUser.id}/grant`, {
        method: 'POST',
        body: JSON.stringify({
          amount: creditAmount,
          description: creditDescription || undefined,
        }),
      });
      if (res.ok) {
        const data = await res.json();
        setUsers(prev => prev.map(user => (
          user.id === creditUser.id
            ? { ...user, credit_balance: data.summary.balance }
            : user
        )));
        toast.success(t('Credits updated'));
        setCreditDialogOpen(false);
      } else {
        const err = await res.json();
        toast.error(formatApiDetail(err.detail, t('Operation failed')));
      }
    } catch (e) {
      console.error(e);
      toast.error(t('Network error'));
    } finally {
      setIsAdjustingCredits(false);
    }
  };

  return (
    <div>
      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:justify-between sm:items-end">
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-indigo-600">{t('Administration')}</p>
          <h1 className="text-2xl font-semibold tracking-tight text-white sm:text-3xl">{t('User Accounts')}</h1>
          <p className="mt-2 text-sm text-gray-400">{t('Manage administrative and standard user accounts, status active/disabled, and roles.')}</p>
        </div>
        <div className="flex flex-wrap gap-3">
          <Button onClick={fetchUsers} variant="outline" className="gap-2 bg-transparent text-slate-700 border-white/20">
            <RefreshCw className="w-4 h-4" /> {t('Refresh')}
          </Button>

          <Dialog open={isCreateOpen} onOpenChange={(open) => {
            setIsCreateOpen(open);
            if (!open) {
              setFormData(emptyForm);
              setErrorMessage('');
            }
          }}>
            <DialogTrigger asChild>
              <Button className="gap-2 bg-indigo-600 hover:bg-indigo-700 text-white border-0">
                <Plus className="w-4 h-4" /> {t('Add User')}
              </Button>
            </DialogTrigger>
            <DialogContent className="sm:max-w-[450px]">
              <DialogHeader>
                <DialogTitle>{t('Create New User Account')}</DialogTitle>
              </DialogHeader>
              <form onSubmit={handleCreate} className="space-y-4 mt-4">
                {errorMessage && (
                  <div className="p-3 bg-red-500/10 border border-red-500/20 text-red-500 rounded-lg text-sm flex items-center gap-2">
                    <ShieldAlert className="w-4 h-4 shrink-0" />
                    <span>{errorMessage}</span>
                  </div>
                )}

                <div className="space-y-2">
                  <Label>{t('Username')} *</Label>
                  <Input
                    required
                    type="text"
                    value={formData.username}
                    onChange={e => setFormData({...formData, username: e.target.value})}
                    placeholder={t('e.g. zhangsan')}
                  />
                </div>

                <div className="space-y-2">
                  <Label>{t('Password')} *</Label>
                  <Input
                    required
                    type="password"
                    value={formData.password}
                    onChange={e => setFormData({...formData, password: e.target.value})}
                    placeholder={t('Minimum 6 characters')}
                  />
                </div>

                <div className="space-y-2">
                  <Label>{t('Display Name')}</Label>
                  <Input
                    type="text"
                    value={formData.display_name}
                    onChange={e => setFormData({...formData, display_name: e.target.value})}
                    placeholder={t('e.g. 张三')}
                  />
                </div>

                <div className="space-y-2">
                  <Label>{t('Initial Credits')}</Label>
                  <Input
                    type="number"
                    min={0}
                    value={formData.initial_credits}
                    onChange={e => setFormData({...formData, initial_credits: Number(e.target.value)})}
                  />
                </div>

                <div className="flex gap-6 pt-2 pb-2">
                  <label className="flex items-center gap-2 text-sm cursor-pointer select-none">
                    <input
                      type="checkbox"
                      checked={formData.is_admin}
                      onChange={e => setFormData({...formData, is_admin: e.target.checked})}
                      className="accent-indigo-500 w-4 h-4 rounded border-slate-300"
                    />
                    <div className="flex items-center gap-1">
                      <Shield className="w-4 h-4 text-indigo-500" />
                      <span>{t('Set as Administrator')}</span>
                    </div>
                  </label>
                </div>

                <div className="pt-4 flex justify-end">
                  <Button type="submit" disabled={isCreating} className="bg-indigo-600 hover:bg-indigo-700 text-white">
                    {isCreating ? t('Creating...') : t('Create Account')}
                  </Button>
                </div>
              </form>
            </DialogContent>
          </Dialog>
        </div>
      </div>

      {isLoading ? (
        <div className="py-20 text-center text-gray-500">{t('Loading user accounts...')}</div>
      ) : users.length === 0 ? (
        <div className="glass-panel p-12 text-center text-gray-400 rounded-lg border border-dashed border-white/20">
          <UserCog className="w-12 h-12 mx-auto mb-4 opacity-50" />
          <p>{t('No user accounts found.')}</p>
        </div>
      ) : (
        <div className="glass-panel rounded-lg overflow-hidden border border-white/10">
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="border-b border-white/10 bg-slate-50 text-xs font-semibold text-slate-400 uppercase tracking-wider">
                  <th className="px-6 py-4">{t('ID')}</th>
                  <th className="px-6 py-4">{t('Username')}</th>
                  <th className="px-6 py-4">{t('Display Name')}</th>
                  <th className="px-6 py-4">{t('Role')}</th>
                  <th className="px-6 py-4">{t('Credits')}</th>
                  <th className="px-6 py-4">{t('Status')}</th>
                  <th className="px-6 py-4 text-right">{t('Actions')}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5 text-sm text-slate-300">
                {users.map(user => {
                  const isSelf = currentUser?.id === user.id;
                  return (
                    <tr key={user.id} className="hover:bg-slate-50 transition-colors">
                      <td className="px-6 py-4 font-mono text-xs">{user.id}</td>
                      <td className="px-6 py-4 font-semibold text-white">
                        <div className="flex items-center gap-2">
                          <span>{user.username}</span>
                          {isSelf && (
                            <span className="text-[10px] bg-slate-500/20 text-slate-300 border border-slate-500/30 px-1.5 py-0.5 rounded">
                              {t('Current')}
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="px-6 py-4">{user.display_name || '-'}</td>
                      <td className="px-6 py-4">
                        {user.is_admin ? (
                          <span className="inline-flex items-center gap-1 text-xs text-indigo-400 bg-indigo-500/10 border border-indigo-500/20 px-2 py-0.5 rounded-full">
                            <Shield className="w-3 h-3" /> {t('Admin')}
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-xs text-slate-400 bg-slate-500/10 border border-slate-500/20 px-2 py-0.5 rounded-full">
                            <User className="w-3 h-3" /> {t('User')}
                          </span>
                        )}
                      </td>
                      <td className="px-6 py-4">
                        <span className="inline-flex items-center gap-1.5 rounded-full border border-indigo-500/20 bg-indigo-500/10 px-2 py-0.5 text-xs font-semibold text-indigo-400">
                          <WalletCards className="h-3 w-3" />
                          {user.credit_balance ?? 0}
                        </span>
                      </td>
                      <td className="px-6 py-4">
                        {user.is_active ? (
                          <span className="inline-flex items-center gap-1 text-xs text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 px-2 py-0.5 rounded-full">
                            <CheckCircle2 className="w-3 h-3" /> {t('Active')}
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-xs text-amber-400 bg-amber-500/10 border border-amber-500/20 px-2 py-0.5 rounded-full">
                            <XCircle className="w-3 h-3" /> {t('Disabled')}
                          </span>
                        )}
                      </td>
                      <td className="px-6 py-4 text-right">
                        <div className="flex justify-end gap-3">
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => handleCreditClick(user)}
                            className="h-8 gap-1 text-xs bg-transparent text-indigo-400 border-indigo-500/20 hover:bg-indigo-500/10"
                          >
                            <WalletCards className="w-3.5 h-3.5" />
                            {t('Credits')}
                          </Button>
                          <Button
                            variant="outline"
                            size="sm"
                            disabled={isSelf}
                            onClick={() => toggleActive(user.id)}
                            className={`h-8 text-xs ${
                              user.is_active
                                ? 'bg-transparent text-amber-500 border-amber-500/20 hover:bg-amber-500/10'
                                : 'bg-transparent text-emerald-400 border-emerald-500/20 hover:bg-emerald-500/10'
                            }`}
                          >
                            {user.is_active ? t('Disable') : t('Enable')}
                          </Button>
                          <Button
                            variant="outline"
                            size="sm"
                            disabled={isSelf}
                            onClick={() => handleDeleteClick(user.id)}
                            className="h-8 text-xs bg-transparent text-red-400 border-red-500/20 hover:bg-red-500/10"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </Button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <ConfirmDialog
        open={deleteDialogOpen}
        title={t('Confirm Delete')}
        message={t('Are you sure you want to delete this user account? This action cannot be undone.')}
        variant="danger"
        onConfirm={handleDeleteConfirm}
        onCancel={() => {
          setDeleteDialogOpen(false);
          setDeleteUserId(null);
        }}
      />

      <Dialog open={creditDialogOpen} onOpenChange={setCreditDialogOpen}>
        <DialogContent className="sm:max-w-[420px]">
          <DialogHeader>
            <DialogTitle>{t('Adjust Credits')}</DialogTitle>
          </DialogHeader>
          <form onSubmit={handleCreditSubmit} className="mt-4 space-y-4">
            <div className="rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-2 text-sm text-indigo-700">
              <div className="font-medium">{creditUser?.username}</div>
              <div className="text-xs text-indigo-700/70">
                {t('Current Balance')}: {creditUser?.credit_balance ?? 0}
              </div>
            </div>

            <div className="space-y-2">
              <Label>{t('Amount')}</Label>
              <Input
                type="number"
                value={creditAmount}
                onChange={e => setCreditAmount(Number(e.target.value))}
                placeholder="100"
                required
              />
              <p className="text-xs text-slate-500">{t('Use negative numbers to deduct credits.')}</p>
            </div>

            <div className="space-y-2">
              <Label>{t('Description')}</Label>
              <Input
                value={creditDescription}
                onChange={e => setCreditDescription(e.target.value)}
                placeholder={t('e.g. Trial top-up or refund adjustment')}
              />
            </div>

            <div className="flex justify-end gap-2 pt-2">
              <Button type="button" variant="outline" onClick={() => setCreditDialogOpen(false)}>
                {t('Cancel')}
              </Button>
              <Button type="submit" disabled={isAdjustingCredits} className="bg-indigo-600 text-white hover:bg-indigo-700">
                {isAdjustingCredits ? t('Saving...') : t('Save')}
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
