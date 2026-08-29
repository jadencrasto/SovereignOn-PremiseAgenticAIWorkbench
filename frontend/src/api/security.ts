/**
 * frontend/src/api/security.ts
 * -----------------------------
 * Security Diagnostics API client.
 */

import type { SecurityStatusResponse } from '../types';

const BASE_URL = '/api/security';

export async function fetchSecurityStatusApi(): Promise<SecurityStatusResponse> {
  const resp = await fetch(`${BASE_URL}/status`, {
    headers: { 'X-Requested-With': 'XMLHttpRequest' },
  });

  if (!resp.ok) {
    throw new Error(`Failed to load security diagnostics (${resp.status})`);
  }

  return resp.json();
}
