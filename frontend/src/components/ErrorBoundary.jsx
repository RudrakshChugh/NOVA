// The last line of defence. Without it a render error anywhere unmounts the whole tree and the
// reviewer is left looking at a blank page with their place in the queue gone. This keeps the
// frame of the app, says what happened in plain words, and offers the one thing that helps.

import { Component } from 'react'

export default class ErrorBoundary extends Component {
  state = { error: null }

  static getDerivedStateFromError(error) { return { error } }

  componentDidCatch(error, info) { console.error('Interface error', error, info?.componentStack) }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="boot">
        <section className="card">
          <header className="card-head">
            <div className="ht">
              <h3>This screen hit a problem</h3>
              <p className="hint">Nothing was lost on the server — decisions and drafts are saved as they are made.</p>
            </div>
          </header>
          <div className="card-body">
            <p className="mono" style={{ whiteSpace: 'pre-wrap', color: 'var(--bad-600)' }}>
              {String(this.state.error?.message || this.state.error)}
            </p>
            <div className="row">
              <button className="btn btn-primary" onClick={() => window.location.reload()}>Reload the app</button>
              <button className="btn btn-ghost" onClick={() => this.setState({ error: null })}>Try again</button>
            </div>
          </div>
        </section>
      </div>
    )
  }
}
