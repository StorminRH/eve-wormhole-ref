// useState lets us store values that change over time (like which filter is active).
// useEffect lets us run code after the page loads (like fetching data).
import { useState, useEffect } from 'react'
import SiteCard from './components/SiteCard'
import './App.css'

const WH_CLASSES = ['All', 'C1', 'C2', 'C3', 'C4', 'C5', 'C6', 'Classless']
const SITE_TYPES = ['All', 'Cosmic Anomaly', 'Magnetometric', 'Radar', 'Gravimetric', 'Ladar']

function App() {
  // Each useState call creates one piece of state.
  // [value, setterFunction] = useState(initialValue)
  const [sites, setSites]         = useState([])       // all sites from the JSON
  const [loading, setLoading]     = useState(true)     // true while fetching
  const [error, setError]         = useState(null)     // holds any fetch error
  const [whClass, setWhClass]     = useState('All')    // active class filter
  const [siteType, setSiteType]   = useState('All')    // active type filter

  // useEffect runs once after the component first renders (the empty [] at the end).
  // This is where we load data — we don't want to fetch on every re-render.
  useEffect(() => {
    fetch('/sites.json')
      .then(res => {
        if (!res.ok) throw new Error('Failed to load site data')
        return res.json()
      })
      .then(data => {
        setSites(data)
        setLoading(false)
      })
      .catch(err => {
        setError(err.message)
        setLoading(false)
      })
  }, [])

  // Derive filtered list from the current filter state.
  // This recalculates automatically whenever sites, whClass, or siteType changes.
  const filtered = sites.filter(site => {
    const classMatch = whClass === 'All' || site.wh_class === whClass
    const typeMatch  = siteType === 'All' || site.site_type === siteType
    return classMatch && typeMatch
  })

  if (loading) return <div className="app"><p>Loading sites...</p></div>
  if (error)   return <div className="app"><p>Error: {error}</p></div>

  return (
    <div className="app">
      <header className="header">
        <h1>Wormhole Site Reference</h1>
        <p>EVE Online — {sites.length} sites indexed from eve-survival.org</p>
      </header>

      <div className="filters">
        <div className="filter-group">
          <span className="filter-label">Class</span>
          {WH_CLASSES.map(cls => (
            // Each button is its own element — we use .map() to generate them
            // from the array instead of writing each one manually
            <button
              key={cls}
              className={`filter-btn ${whClass === cls ? 'active' : ''}`}
              onClick={() => setWhClass(cls)}
            >
              {cls}
            </button>
          ))}
        </div>

        <div className="filter-group">
          <span className="filter-label">Type</span>
          {SITE_TYPES.map(type => (
            <button
              key={type}
              className={`filter-btn ${siteType === type ? 'active' : ''}`}
              onClick={() => setSiteType(type)}
            >
              {type}
            </button>
          ))}
        </div>
      </div>

      <p className="results-count">
        Showing {filtered.length} of {sites.length} sites
      </p>

      <div className="site-grid">
        {filtered.map(site => (
          // key is required when rendering lists — React uses it to track
          // which items changed, were added, or were removed efficiently
          <SiteCard key={site.name} site={site} />
        ))}
      </div>
    </div>
  )
}

export default App
