import { useState } from 'react'

const TAG_LABELS = {
  web:   'Web',
  scram: 'Scram',
  nos:   'NOS',
  rep:   'Reps',
  reps:  'Reps',
}

function formatISK(isk) {
  if (!isk) return null
  if (isk >= 1_000_000_000) return `${(isk / 1_000_000_000).toFixed(1)}B ISK`
  if (isk >= 1_000_000)     return `${(isk / 1_000_000).toFixed(1)}M ISK`
  return `${isk.toLocaleString()} ISK`
}

function formatEHP(ehp) {
  if (!ehp) return null
  if (ehp >= 1_000_000) return `${(ehp / 1_000_000).toFixed(1)}M EHP`
  if (ehp >= 1_000)     return `${Math.round(ehp / 1000)}k EHP`
  return `${ehp} EHP`
}

function EwarRow({ waves, webbers, scramblers }) {
  // Collect ewar types: site-level text fields + per-NPC tags
  const tags = new Set()

  if (webbers   && webbers   !== 'None') tags.add('web')
  if (scramblers && scramblers !== 'None') tags.add('scram')

  for (const wave of waves) {
    for (const npc of wave.npcs ?? []) {
      for (const t of npc.tags ?? []) tags.add(t)
    }
  }

  if (tags.size === 0) return null

  return (
    <div className="ewar-row">
      <span className="ewar-label">EWAR</span>
      {[...tags].map(tag => (
        <span key={tag} className={`tag tag-${tag}`}>
          {TAG_LABELS[tag] ?? tag}
        </span>
      ))}
    </div>
  )
}

function NpcRow({ npc }) {
  const ehp = formatEHP(npc.ehp)
  return (
    <div className="npc-row">
      <span className="npc-count">{npc.count}x</span>
      <span className="npc-name">{npc.name}</span>
      {ehp && <span className="npc-ehp">{ehp}</span>}
      <span className="tags">
        {npc.trigger && <span className="tag tag-trigger">Trigger</span>}
        {(npc.tags ?? []).map(tag => (
          <span key={tag} className={`tag tag-${tag}`}>
            {TAG_LABELS[tag] ?? tag}
          </span>
        ))}
      </span>
    </div>
  )
}

function WaveSection({ wave, isLast }) {
  return (
    <>
      <div className="wave-section">
        <div className="wave-header">
          <span className="wave-name">{wave.name}</span>
          {wave.dps > 0 && (
            <span className="wave-dps">DPS <span>{wave.dps}</span></span>
          )}
        </div>
        <div className="npc-list">
          {(wave.npcs ?? []).map((npc, i) => (
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

  const waves      = Array.isArray(site.waves) ? site.waves : []
  const lootDisplay = formatISK(site.loot_isk)

  return (
    <div className={`site-card ${open ? 'open' : ''}`}>

      <div className="card-summary" onClick={() => setOpen(o => !o)}>
        <div className="summary-left">
          <div className="card-title">{site.display_name}</div>
          <div className="card-subtitle">
            {site.site_type && <span className="type-tag">{site.site_type}</span>}
            {site.wh_class  && (
              <span className={`badge badge-${site.wh_class}`}>{site.wh_class}</span>
            )}
          </div>
        </div>
        {lootDisplay && <span className="stat-loot">{lootDisplay}</span>}
        <span className="chevron">▼</span>
      </div>

      {open && (
        <>
          <div className="card-divider" />
          <div className="card-detail">

            <EwarRow
              waves={waves}
              webbers={site.webbers}
              scramblers={site.scramblers}
            />

            {waves.length > 0
              ? waves.map((wave, i) => (
                  <WaveSection
                    key={i}
                    wave={wave}
                    isLast={i === waves.length - 1}
                  />
                ))
              : <p className="no-data">No wave data available.</p>
            }

          </div>
        </>
      )}

    </div>
  )
}

export default SiteCard
