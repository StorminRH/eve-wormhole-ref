import { useState } from 'react'

const TAG_LABELS = { web: 'Web', scram: 'Scram', nos: 'NOS' }

function formatISK(isk) {
  if (!isk) return null
  if (isk >= 1_000_000_000) return `${(isk / 1_000_000_000).toFixed(1)}B ISK`
  if (isk >= 1_000_000)     return `${(isk / 1_000_000).toFixed(1)}M ISK`
  return `${isk.toLocaleString()} ISK`
}

function NpcRow({ npc }) {
  return (
    <div className="npc-row">
      <span className="npc-count">{npc.count}x</span>
      <span className="npc-type">{npc.ship_type}</span>
      <span className="npc-name">{npc.name}</span>
      <span className="tags">
        {npc.trigger && <span className="tag tag-trigger">Trigger</span>}
        {npc.tags.map(tag => (
          <span key={tag} className={`tag tag-${tag}`}>
            {TAG_LABELS[tag] ?? tag}
          </span>
        ))}
      </span>
    </div>
  )
}

function WaveSection({ pocket, isLast }) {
  const dps = pocket.max_dps ?? pocket.initial_dps
  return (
    <>
      <div className="wave-section">
        <div className="wave-header">
          <span className="wave-name">{pocket.name}</span>
          {dps && <span className="wave-dps">DPS <span>{dps}</span></span>}
        </div>
        <div className="npc-list">
          {pocket.npcs.map((npc, i) => (
            <NpcRow key={i} npc={npc} />
          ))}
        </div>
      </div>
      {!isLast && <div className="wave-sep" />}
    </>
  )
}

function SiteCard({ site }) {
  const [open, setOpen] = useState(false)

  // Only show meta rows that have meaningful values
  const metaRows = [
    { label: 'Webbers',    value: site.webbers,    className: site.webbers    && site.webbers    !== 'None' ? 'danger' : '' },
    { label: 'Scramblers', value: site.scramblers, className: site.scramblers && site.scramblers !== 'None' ? 'danger' : '' },
    { label: 'Ships',      value: site.recommended_ships, className: '' },
  ].filter(r => r.value)

  // waves is stored as JSONB in the DB — it holds the eve-survival pocket structure
  const pockets = Array.isArray(site.waves) && typeof site.waves[0] === 'object'
    ? site.waves
    : []

  const lootDisplay = formatISK(site.loot_isk)

  return (
    <div className={`site-card ${open ? 'open' : ''}`}>

      <div className="card-summary" onClick={() => setOpen(o => !o)}>
        <div className="summary-left">
          <div className="card-title">{site.display_name}</div>
          <div className="card-subtitle">
            <span className="type-tag">{site.site_type}</span>
            <span className={`badge badge-${site.wh_class}`}>{site.wh_class}</span>
          </div>
        </div>
        {lootDisplay && (
          <div className="summary-stats">
            <span className="stat-loot">{lootDisplay}</span>
          </div>
        )}
        <span className="chevron">▼</span>
      </div>

      {open && (
        <>
          <div className="card-divider" />
          <div className="card-detail">

            {metaRows.length > 0 && (
              <div className="meta-grid">
                {metaRows.map(({ label, value, className }) => (
                  <>
                    <span key={label + '-k'} className="meta-key">{label}</span>
                    <span key={label + '-v'} className={`meta-val ${className}`}>{value}</span>
                  </>
                ))}
              </div>
            )}

            {pockets.map((pocket, i) => (
              <WaveSection
                key={pocket.name}
                pocket={pocket}
                isLast={i === pockets.length - 1}
              />
            ))}

          </div>
        </>
      )}

    </div>
  )
}

export default SiteCard
