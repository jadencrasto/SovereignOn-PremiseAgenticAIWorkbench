import React, { useEffect, useState } from 'react';
import { useAuth } from '../../context/AuthContext';
import { useWorkbench } from '../../context/WorkbenchContext';
import { apiRequest } from '../../api/client';
import { capabilityFor, describeCapability, type CapabilityName } from '../../api/capabilities';

type PermissionReport = { role: string; permissions: Record<string, boolean> };
const labels: Record<string, string> = {
  view_data: 'Request access to data', execute_read_tools: 'Request read tools',
  execute_write_tools: 'Request write tools', approve_tasks: 'Request task approval',
  manage_tasks: 'Manage tasks', manage_users: 'Manage users',
  manage_config: 'Manage configuration', view_security: 'View security information',
};
const features: { key: CapabilityName; label: string }[] = [
  { key: 'chat', label: 'Text chat' }, { key: 'multimodal', label: 'Image chat' },
  { key: 'document_rag', label: 'Document retrieval' },
  { key: 'approval_resume', label: 'Approval and resume' },
  { key: 'artifact_access', label: 'Artifact access' },
  { key: 'code_execution', label: 'Generated code execution' },
];

export const RolePortalView: React.FC = () => {
  const { user, isLoading } = useAuth();
  const { capabilities, isCheckingHealth } = useWorkbench();
  const [loaded, setReport] = useState<{ userId: string; value: PermissionReport } | null>(null);
  const report = loaded?.userId === user?.id ? loaded.value : null;
  const [error, setError] = useState(false);
  useEffect(() => {
    let active = true;
    setReport(null);
    setError(false);
    if (isLoading || !user) return () => { active = false; };
    void apiRequest<PermissionReport>('/api/auth/me/permissions').then(value => {
      if (active) setReport({ userId: user.id, value });
    }).catch(() => { if (active) setError(true); });
    return () => { active = false; };
  }, [user?.id, isLoading]);

  return <div className="flex-1 overflow-y-auto bg-slate-50 p-6 text-slate-900">
    <div className="mx-auto max-w-4xl space-y-5">
      <h1 className="text-xl font-semibold">Your access</h1>
      <p className="text-sm text-slate-600">Permissions describe requests your role may make. Each operation still requires server authorization and may be blocked or require approval.</p>
      {isLoading || (!report && !error && user) ? <p role="status">Loading access information...</p> : null}
      {!user && !isLoading && <p role="status">Sign in to view access information.</p>}
      {error && <p role="alert">Access information is unavailable. Please try again later.</p>}
      {report && user && <section className="rounded-xl border bg-white p-5">
        <h2 className="font-semibold">Signed-in role: {report.role}</h2>
        <ul className="mt-3 space-y-2 text-sm">{Object.entries(labels).map(([key, label]) =>
          <li key={key}>{label}: {report.permissions[key] === true ? 'Permitted by role' : 'Not permitted by role'}</li>)}</ul>
      </section>}
      <section className="rounded-xl border bg-white p-5">
        <h2 className="font-semibold">Feature status</h2>
        <ul className="mt-3 space-y-3 text-sm">{features.map(({ key, label }) => {
          const status = capabilityFor(capabilities, key, !isCheckingHealth);
          return <li key={key}><strong>{label}: {status.state}</strong><p className="text-slate-600">{describeCapability(status)}</p></li>;
        })}</ul>
      </section>
      <p className="text-xs text-slate-600">Model inventory does not establish that inference works. Account changes are not available in this view.</p>
    </div>
  </div>;
};
