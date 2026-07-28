import React from 'react';
import { NavLink } from 'react-router-dom';
import { 
  LayoutDashboard, 
  Users, 
  Network, 
  ActivitySquare, 
  CheckSquare, 
  Database,
  Settings
} from 'lucide-react';

export const Layout: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  return (
    <div className="app-container">
      <aside className="sidebar">
        <div style={{ padding: '20px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: '12px' }}>
          <img src="/brand/icon-192.png" alt="MESA" width={38} height={38} style={{ borderRadius: '10px', display: 'block' }} />
          <div>
            <h2 className="text-gradient" style={{ fontSize: '1.25rem' }}>MESA Control</h2>
            <p style={{ color: 'var(--text-muted)', fontSize: '0.75rem', marginTop: '4px' }}>v0.3.0</p>
          </div>
        </div>
        
        <nav style={{ padding: '16px 0', flex: 1, display: 'flex', flexDirection: 'column', gap: '4px' }}>
          <NavLink to="/overview" className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
            <LayoutDashboard size={18} />
            Overview
          </NavLink>
          
          <NavLink to="/clients" className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
            <Users size={18} />
            Clients
          </NavLink>
          
          <NavLink to="/connections" className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
            <Network size={18} />
            Connections
          </NavLink>
          
          <NavLink to="/activity" className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
            <ActivitySquare size={18} />
            Activity Log
          </NavLink>
          
          <NavLink to="/approvals" className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
            <CheckSquare size={18} />
            Approvals
          </NavLink>
          
          <NavLink to="/memories" className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
            <Database size={18} />
            Memories
          </NavLink>
        </nav>
        
        <div style={{ padding: '16px 0', borderTop: '1px solid var(--border)' }}>
          <NavLink to="/settings" className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
            <Settings size={18} />
            Settings
          </NavLink>
        </div>
      </aside>
      
      <main className="main-content">
        <div className="page-container">
          {children}
        </div>
      </main>
    </div>
  );
};
