// Finds the paragraphs that decide what a tenant pays in CAM and pulls out the
// numbers a reviewer needs first. Heuristic: every hit carries its quote so a
// person (or the CAM parser downstream) can check it against the lease.
const RULES = {
  pro_rata_share: /(pro rata|proportionate) share/i,
  cam_cap: /(increase|exceed)[^.]{0,120}(\d+(\.\d+)?\s*%|percent)[^.]{0,80}(controllable|operating|common area)|(controllable|common area)[^.]{0,120}(shall not|not to) (increase|exceed)/i,
  admin_fee: /(administrative|management|overhead)[^.]{0,60}(fee|charge)[^.]{0,120}(\d+(\.\d+)?\s*%|percent)/i,
  capital_exclusion: /capital (improvement|expenditure|repair|replacement)/i,
  real_estate_taxes: /real (estate|property) tax/i,
  audit_right: /(audit|inspect|examine)[^.]{0,80}(books|records)/i,
};

const pct = (s) => [...s.matchAll(/(\d+(?:\.\d+)?)\s*(?:%|percent)/gi)].map((m) => Number(m[1]));

export function extractClauses(text) {
  const paras = text.split(/\n\s*\n|(?<=\.)\s{2,}/).map((p) => p.replace(/\s+/g, ' ').trim()).filter((p) => p.length > 40);
  const out = {};
  for (const [name, re] of Object.entries(RULES)) {
    const hits = paras.filter((p) => re.test(p)).slice(0, 3);
    if (hits.length) out[name] = hits.map((q) => ({ quote: q.slice(0, 1200), percents: pct(q) }));
  }
  return out;
}
