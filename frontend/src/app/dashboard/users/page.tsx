"use client";

import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import {
  UserCog,
  Plus,
  RefreshCw,
  Trash2,
  CheckCircle2,
  XCircle,
  ShieldAlert,
  User,
  Shield
} from 'lucide-react';
import { apiFetch } from '@/lib/utils';
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

interface CreateUserForm {
  username: string;
  password: string;
  display_name: string;
  is_admin: boolean;
}

const emptyForm: CreateUserForm = {
  username: '',
  password: '',
  display_name: '',
  is_admin: false,
};

export default function UsersPage() {
  const [users, setUsers] = useState<UserType[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [currentUser, setCurrentUser] = useState<any>(null);

  // Form State
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [isCreating, setIsCreating] = useState(false);
  const [formData, setFormData] = useState<CreateUserForm>(emptyForm);
  const [errorMessage, setErrorMessage] = useState('');

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
          setCurrentUser(JSON.parse(userStr));
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
        setErrorMessage(err.detail || 'Failed to create user');
      }
    } catch (e) {
      console.error(e);
      setErrorMessage('Network error, please try again');
    } finally {
      setIsCreating(false);
    }
  };

  const toggleActive = async (id: number) => {
    if (currentUser && currentUser.id === id) {
      toast.error('您不能禁用自己的账号！');
      return;
    }
    try {
      const res = await apiFetch(`/api/auth/users/${id}/toggle-active`, {
        method: 'POST',
      });
      if (res.ok) {
        fetchUsers();
        toast.success('操作成功');
      } else {
        const err = await res.json();
        toast.error(err.detail || '操作失败');
      }
    } catch (e) {
      console.error(e);
      toast.error('网络错误，请重试');
    }
  };

  const deleteUser = async (id: number) => {
    if (currentUser && currentUser.id === id) {
      toast.error('您不能删除自己的账号！');
      return;
    }
    if (!confirm('确定要删除这个账号吗？删除后该用户将无法登录系统，且此操作不可逆。')) return;
    try {
      const res = await apiFetch(`/api/auth/users/${id}`, {
        method: 'DELETE',
      });
      if (res.ok) {
        fetchUsers();
        toast.success('账号已删除');
      } else {
        const err = await res.json();
        toast.error(err.detail || '删除失败');
      }
    } catch (e) {
      console.error(e);
      toast.error('网络错误，请重试');
    }
  };

  return (
    <div>
      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:justify-between sm:items-end">
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-indigo-600">Administration</p>
          <h1 className="text-2xl font-semibold tracking-tight text-white sm:text-3xl">User Accounts</h1>
          <p className="mt-2 text-sm text-gray-400">Manage administrative and standard user accounts, status active/disabled, and roles.</p>
        </div>
        <div className="flex flex-wrap gap-3">
          <Button onClick={fetchUsers} variant="outline" className="gap-2 bg-transparent text-slate-700 border-white/20">
            <RefreshCw className="w-4 h-4" /> Refresh
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
                <Plus className="w-4 h-4" /> Add User
              </Button>
            </DialogTrigger>
            <DialogContent className="sm:max-w-[450px]">
              <DialogHeader>
                <DialogTitle>Create New User Account</DialogTitle>
              </DialogHeader>
              <form onSubmit={handleCreate} className="space-y-4 mt-4">
                {errorMessage && (
                  <div className="p-3 bg-red-500/10 border border-red-500/20 text-red-500 rounded-lg text-sm flex items-center gap-2">
                    <ShieldAlert className="w-4 h-4 shrink-0" />
                    <span>{errorMessage}</span>
                  </div>
                )}
                
                <div className="space-y-2">
                  <Label>Username *</Label>
                  <Input 
                    required 
                    type="text" 
                    value={formData.username} 
                    onChange={e => setFormData({...formData, username: e.target.value})} 
                    placeholder="e.g. zhangsan" 
                  />
                </div>
                
                <div className="space-y-2">
                  <Label>Password *</Label>
                  <Input 
                    required 
                    type="password" 
                    value={formData.password} 
                    onChange={e => setFormData({...formData, password: e.target.value})} 
                    placeholder="Minimum 6 characters" 
                  />
                </div>

                <div className="space-y-2">
                  <Label>Display Name</Label>
                  <Input 
                    type="text" 
                    value={formData.display_name} 
                    onChange={e => setFormData({...formData, display_name: e.target.value})} 
                    placeholder="e.g. 张三" 
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
                      <span>Set as Administrator</span>
                    </div>
                  </label>
                </div>

                <div className="pt-4 flex justify-end">
                  <Button type="submit" disabled={isCreating} className="bg-indigo-600 hover:bg-indigo-700 text-white">
                    {isCreating ? 'Creating...' : 'Create Account'}
                  </Button>
                </div>
              </form>
            </DialogContent>
          </Dialog>
        </div>
      </div>

      {isLoading ? (
        <div className="py-20 text-center text-gray-500">Loading user accounts...</div>
      ) : users.length === 0 ? (
        <div className="glass-panel p-12 text-center text-gray-400 rounded-lg border border-dashed border-white/20">
          <UserCog className="w-12 h-12 mx-auto mb-4 opacity-50" />
          <p>No user accounts found.</p>
        </div>
      ) : (
        <div className="glass-panel rounded-lg overflow-hidden border border-white/10">
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="border-b border-white/10 bg-white/5 text-xs font-semibold text-slate-400 uppercase tracking-wider">
                  <th className="px-6 py-4">ID</th>
                  <th className="px-6 py-4">Username</th>
                  <th className="px-6 py-4">Display Name</th>
                  <th className="px-6 py-4">Role</th>
                  <th className="px-6 py-4">Status</th>
                  <th className="px-6 py-4 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5 text-sm text-slate-300">
                {users.map(user => {
                  const isSelf = currentUser && currentUser.id === user.id;
                  return (
                    <tr key={user.id} className="hover:bg-white/5 transition-colors">
                      <td className="px-6 py-4 font-mono text-xs">{user.id}</td>
                      <td className="px-6 py-4 font-semibold text-white">
                        <div className="flex items-center gap-2">
                          <span>{user.username}</span>
                          {isSelf && (
                            <span className="text-[10px] bg-slate-500/20 text-slate-300 border border-slate-500/30 px-1.5 py-0.5 rounded">
                              Current
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="px-6 py-4">{user.display_name || '-'}</td>
                      <td className="px-6 py-4">
                        {user.is_admin ? (
                          <span className="inline-flex items-center gap-1 text-xs text-indigo-400 bg-indigo-500/10 border border-indigo-500/20 px-2 py-0.5 rounded-full">
                            <Shield className="w-3 h-3" /> Admin
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-xs text-slate-400 bg-slate-500/10 border border-slate-500/20 px-2 py-0.5 rounded-full">
                            <User className="w-3 h-3" /> User
                          </span>
                        )}
                      </td>
                      <td className="px-6 py-4">
                        {user.is_active ? (
                          <span className="inline-flex items-center gap-1 text-xs text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 px-2 py-0.5 rounded-full">
                            <CheckCircle2 className="w-3 h-3" /> Active
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-xs text-amber-400 bg-amber-500/10 border border-amber-500/20 px-2 py-0.5 rounded-full">
                            <XCircle className="w-3 h-3" /> Disabled
                          </span>
                        )}
                      </td>
                      <td className="px-6 py-4 text-right">
                        <div className="flex justify-end gap-3">
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
                            {user.is_active ? 'Disable' : 'Enable'}
                          </Button>
                          <Button 
                            variant="outline" 
                            size="sm" 
                            disabled={isSelf}
                            onClick={() => deleteUser(user.id)}
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
    </div>
  );
}
