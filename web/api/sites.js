// Vercel Serverless Function — GET /api/sites
//
// Queries the PostgreSQL database and returns site records as JSON.
// Supports optional query params: ?wh_class=C3&site_type=Relic
//
// The DATABASE_URL environment variable must be set:
//   - Local dev:  set in .env.local (points to Docker)
//   - Production: set in Vercel dashboard (points to Neon)

import pg from 'pg';
const { Pool } = pg;

// Pool is created once and reused across warm invocations.
// Vercel may keep a function instance alive between requests,
// so this avoids opening a new connection every time.
let pool;
function getPool() {
  if (!pool) {
    pool = new Pool({
      connectionString: process.env.DATABASE_URL,
      // SSL is required for Neon (production) but not for local Docker
      ssl: process.env.DATABASE_URL?.includes('neon.tech')
        ? { rejectUnauthorized: false }
        : false,
    });
  }
  return pool;
}

export default async function handler(req, res) {
  // Allow the Vite dev server to call this during local development
  res.setHeader('Access-Control-Allow-Origin', '*');

  const { wh_class, site_type } = req.query;

  const conditions = [];
  const params = [];

  if (wh_class && wh_class !== 'All') {
    params.push(wh_class);
    conditions.push(`wh_class = $${params.length}`);
  }
  if (site_type && site_type !== 'All') {
    params.push(site_type);
    conditions.push(`site_type = $${params.length}`);
  }

  const where = conditions.length ? `WHERE ${conditions.join(' AND ')}` : '';

  const query = `
    SELECT
      key, display_name, wh_class, site_type,
      faction, damage_dealt, webbers, scramblers,
      recommended_ships, loot_isk, dps, total_ehp, waves
    FROM sites
    ${where}
    ORDER BY wh_class NULLS LAST, site_type, display_name
  `;

  try {
    const result = await getPool().query(query, params);
    res.status(200).json(result.rows);
  } catch (err) {
    console.error('Database error:', err.message);
    res.status(500).json({ error: 'Failed to load sites' });
  }
}
