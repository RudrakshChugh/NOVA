import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
// Bundled, not fetched: the app has to render the same on a lab machine with no route to a font CDN.
import '@fontsource-variable/inter'
import '@fontsource-variable/space-grotesk'
import './index.css'
import App from './App.jsx'
import ErrorBoundary from './components/ErrorBoundary.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
)
