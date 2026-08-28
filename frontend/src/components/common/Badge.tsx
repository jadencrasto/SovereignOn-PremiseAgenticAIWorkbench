import React from 'react';

interface BadgeProps {
  variant?: 'emerald' | 'blue' | 'amber' | 'slate' | 'rose' | 'purple';
  children: React.ReactNode;
  className?: string;
}

export const Badge: React.FC<BadgeProps> = ({ variant = 'slate', children, className = '' }) => {
  const styles = {
    emerald: 'bg-emerald-950/70 text-emerald-400 border-emerald-800/60',
    blue: 'bg-blue-950/70 text-blue-400 border-blue-800/60',
    amber: 'bg-amber-950/70 text-amber-400 border-amber-800/60',
    rose: 'bg-rose-950/70 text-rose-400 border-rose-800/60',
    purple: 'bg-purple-950/70 text-purple-400 border-purple-800/60',
    slate: 'bg-slate-900 text-slate-300 border-slate-700/70',
  };

  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-mono font-medium border ${styles[variant]} ${className}`}
    >
      {children}
    </span>
  );
};
