import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router';
import { Layout } from './components/Layout';
import { Overview } from './pages/Overview';
import { Clients } from './pages/Clients';
import { Connections } from './pages/Connections';
import { Activity } from './pages/Activity';
import { Approvals } from './pages/Approvals';
import { Memories } from './pages/Memories';
import { Settings } from './pages/Settings';

function App() {
  return (
    <Router basename="/dashboard">
      <Layout>
        <Routes>
          <Route path="/" element={<Navigate to="/overview" replace />} />
          <Route path="/overview" element={<Overview />} />
          <Route path="/clients" element={<Clients />} />
          <Route path="/connections" element={<Connections />} />
          <Route path="/activity" element={<Activity />} />
          <Route path="/approvals" element={<Approvals />} />
          <Route path="/memories" element={<Memories />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </Layout>
    </Router>
  );
}

export default App;
