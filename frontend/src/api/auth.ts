/**
 * frontend/src/api/auth.ts
 * -------------------------
 * Authentication and user management API client.
 */

import type { User } from '../types';

const BASE_URL = '/api/auth';

export async function loginApi(username: string, password: string): Promise<{ token: string; user: User }> {
  const resp = await fetch(`${BASE_URL}/login`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Requested-With': 'XMLHttpRequest',
    },
    body: JSON.stringify({ username, password }),
  });

  if (!resp.ok) {
    const errorData = await resp.json().catch(() => ({ detail: 'Authentication failed' }));
    throw new Error(errorData.detail || `Login error (${resp.status})`);
  }

  return resp.json();
}

export async function logoutApi(): Promise<void> {
  await fetch(`${BASE_URL}/logout`, {
    method: 'POST',
    headers: {
      'X-Requested-With': 'XMLHttpRequest',
    },
  });
}

export async function getMeApi(): Promise<User> {
  const resp = await fetch(`${BASE_URL}/me`, {
    headers: {
      'X-Requested-With': 'XMLHttpRequest',
    },
  });

  if (!resp.ok) {
    throw new Error(`Unauthorized (${resp.status})`);
  }

  return resp.json();
}

export async function changePasswordApi(currentPassword: string, newPassword: string): Promise<{ message: string }> {
  const resp = await fetch(`${BASE_URL}/change-password`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Requested-With': 'XMLHttpRequest',
    },
    body: JSON.stringify({
      current_password: currentPassword,
      new_password: newPassword,
    }),
  });

  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: 'Failed to change password' }));
    throw new Error(err.detail || `Error (${resp.status})`);
  }

  return resp.json();
}

export async function listUsersApi(): Promise<User[]> {
  const resp = await fetch(`${BASE_URL}/users`, {
    headers: { 'X-Requested-With': 'XMLHttpRequest' },
  });
  if (!resp.ok) {
    throw new Error('Failed to list users');
  }
  return resp.json();
}
