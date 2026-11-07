/**
 * frontend/src/context/AuthContext.tsx
 * -------------------------------------
 * React Context providing session authentication state, current user,
 * login, logout, and permission checks.
 */

import React, { createContext, useContext, useState, useEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import type { User, UserRole } from '../types';
import { getMeApi, loginApi, logoutApi } from '../api/auth';

interface AuthContextType {
  user: User | null;
  role: UserRole;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refreshUser: () => Promise<void>;
  hasRole: (minRole: UserRole) => boolean;
}

const ROLE_LEVELS: Record<UserRole, number> = {
  viewer: 1,
  operator: 2,
  admin: 3,
};

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const authEpoch = useRef(0);
  const authChannel = useRef<BroadcastChannel | null>(null);

  const refreshUser = async () => {
    const epoch = ++authEpoch.current;
    try {
      const u = await getMeApi();
      if (epoch === authEpoch.current) setUser(u);
    } catch {
      if (epoch === authEpoch.current) setUser(null);
    } finally {
      if (epoch === authEpoch.current) setIsLoading(false);
    }
  };

  useEffect(() => {
    if (typeof BroadcastChannel !== 'undefined') {
      const channel = new BroadcastChannel('sovereign-auth-changes');
      authChannel.current = channel;
      channel.onmessage = (event) => {
        if (event.data === 'changing') {
          authEpoch.current++;
          setIsLoading(true);
          setUser(null);
        } else if (event.data === 'changed') {
          void refreshUser();
        }
      };
      refreshUser();
      return () => { authChannel.current = null; channel.close(); };
    }
    refreshUser();
  }, []);

  const login = async (username: string, password: string) => {
    const epoch = ++authEpoch.current;
    authChannel.current?.postMessage('changing');
    setIsLoading(true);
    setUser(null);
    try {
      const res = await loginApi(username, password);
      if (epoch === authEpoch.current) setUser(res.user);
    } finally {
      if (epoch === authEpoch.current) setIsLoading(false);
      authChannel.current?.postMessage('changed');
    }
  };

  const logout = async () => {
    const epoch = ++authEpoch.current;
    authChannel.current?.postMessage('changing');
    setIsLoading(true);
    setUser(null);
    try {
      await logoutApi();
    } finally {
      if (epoch === authEpoch.current) setIsLoading(false);
      authChannel.current?.postMessage('changed');
    }
  };

  const role: UserRole = user?.role || 'viewer';

  const hasRole = (minRole: UserRole): boolean => {
    const currentLvl = ROLE_LEVELS[role] || 0;
    const requiredLvl = ROLE_LEVELS[minRole] || 999;
    return currentLvl >= requiredLvl;
  };

  return (
    <AuthContext.Provider
      value={{
        user,
        role,
        isAuthenticated: !!user,
        isLoading,
        login,
        logout,
        refreshUser,
        hasRole,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = (): AuthContextType => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};
