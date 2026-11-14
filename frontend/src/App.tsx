import { AuthProvider, useAuth } from './context/AuthContext';
import { WorkbenchProvider } from './context/WorkbenchContext';
import { AppLayout } from './components/layout/AppLayout';

function AuthenticatedWorkbench() {
  const { user, isLoading } = useAuth();
  return (
    <WorkbenchProvider key={isLoading ? 'auth-changing' : (user ? `user:${user.id}` : 'anonymous')}>
      <AppLayout />
    </WorkbenchProvider>
  );
}

function App() {
  return (
    <AuthProvider>
      <AuthenticatedWorkbench />
    </AuthProvider>
  );
}

export default App;
