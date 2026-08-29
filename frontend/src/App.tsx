import { AuthProvider } from './context/AuthContext';
import { WorkbenchProvider } from './context/WorkbenchContext';
import { AppLayout } from './components/layout/AppLayout';

function App() {
  return (
    <AuthProvider>
      <WorkbenchProvider>
        <AppLayout />
      </WorkbenchProvider>
    </AuthProvider>
  );
}

export default App;
