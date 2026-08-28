import { WorkbenchProvider } from './context/WorkbenchContext';
import { AppLayout } from './components/layout/AppLayout';

function App() {
  return (
    <WorkbenchProvider>
      <AppLayout />
    </WorkbenchProvider>
  );
}

export default App;
