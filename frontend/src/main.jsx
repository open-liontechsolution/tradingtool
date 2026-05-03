import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import { AuthProvider } from './auth/AuthProvider'
import { ToastProvider } from './components/ToastProvider'
import { RecommendationApplyProvider } from './components/RecommendationApplyProvider'
import App from './App.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <AuthProvider>
      <ToastProvider>
        <RecommendationApplyProvider>
          <App />
        </RecommendationApplyProvider>
      </ToastProvider>
    </AuthProvider>
  </StrictMode>,
)
