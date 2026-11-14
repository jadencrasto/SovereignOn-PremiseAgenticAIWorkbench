import React from 'react';
import type { SourceReference } from '../../types';

interface SourceCardProps {
  source: SourceReference;
  index?: number;
}

// The current API cannot revalidate source authorization and provenance.
// Suppress even metadata until a verified presentation contract exists.
export const SourceCard: React.FC<SourceCardProps> = () => (
  <div role="status" className="rounded-lg border border-slate-200 bg-white p-3 text-xs text-slate-600">
    Source details unavailable: document access and citation provenance cannot currently be verified.
  </div>
);
