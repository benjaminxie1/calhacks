import { Actor, log } from 'apify';
import * as cheerio from 'cheerio';
import { extractClauses } from './clauses.js';

await Actor.init();
const input = (await Actor.getInput()) ?? {};
const {
  query = '"common area maintenance" "shopping center" "proportionate share"',
  forms = '10-K,10-Q,8-K',
  startDate = '2001-01-01',
  maxResults = 10,
  includeText = false,
  userAgent = 'EDGAR Lease Finder (Apify Actor) contact@example.com',
} = input;

const headers = { 'User-Agent': userAgent, Accept: 'application/json,text/html' };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// SEC fair access: identify ourselves and stay well under 10 requests/second.
async function get(url) {
  for (let attempt = 0; attempt < 4; attempt++) {
    const res = await fetch(url, { headers });
    if (res.ok) return res;
    if (res.status !== 429 && res.status < 500) throw new Error(`${res.status} for ${url}`);
    await sleep(1000 * 2 ** attempt);
  }
  throw new Error(`gave up on ${url}`);
}

const params = new URLSearchParams({ q: query, forms, dateRange: 'custom', startdt: startDate,
  enddt: new Date().toISOString().slice(0, 10) });
const search = await (await get(`https://efts.sec.gov/LATEST/search-index?${params}`)).json();
const hits = (search.hits?.hits ?? []).filter((h) => /^EX-10/i.test(h._source?.file_type ?? '') || /lease/i.test(h._id));
log.info(`EDGAR returned ${search.hits?.total?.value ?? 0} filings, ${hits.length} look like lease exhibits`);

let pushed = 0;
for (const h of hits) {
  if (pushed >= maxResults) break;
  const [accession, file] = h._id.split(':');
  const src = h._source;
  const cik = String(src.ciks?.[0] ?? '').replace(/^0+/, '');
  const url = `https://www.sec.gov/Archives/edgar/data/${cik}/${accession.replace(/-/g, '')}/${file}`;
  try {
    await sleep(250);
    const raw = await (await get(url)).text();
    const text = /<html|<body|<p/i.test(raw) ? cheerio.load(raw)('body').text() : raw;
    const clauses = extractClauses(text);
    if (!clauses.pro_rata_share && !clauses.cam_cap) continue;
    await Actor.pushData({
      company: src.display_names?.[0] ?? null, cik, accession, form: src.root_forms?.[0] ?? null,
      exhibit: src.file_type, file_date: src.file_date, url, chars: text.length, clauses,
      ...(includeText ? { text } : {}),
    });
    pushed++;
  } catch (e) {
    log.warning(`skipped ${url}: ${e.message}`);
  }
}
log.info(`pushed ${pushed} leases`);
await Actor.exit();
