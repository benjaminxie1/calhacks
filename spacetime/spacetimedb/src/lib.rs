//! Dispute room: the live, shared record of one CAM audit. The agents write
//! findings and every trail step here; the tenant (or their accountant) watches
//! it fill in and answers the agent's open questions by calling `rule`.
use spacetimedb::{ReducerContext, Table, Timestamp};

#[spacetimedb::table(accessor = audit, public)]
pub struct Audit {
    #[primary_key]
    pub key: String,
    pub tenant: String,
    pub year: u32,
    pub landlord_says_due: f64,
    pub actually_due: f64,
    pub status: String,
    pub opened_at: Timestamp,
}

#[spacetimedb::table(accessor = finding, public,
    index(accessor = by_audit_rule, btree(columns = [audit_key, rule])))]
pub struct Finding {
    #[primary_key]
    #[auto_inc]
    pub id: u64,
    #[index(btree)]
    pub audit_key: String,
    pub rule: String,
    pub title: String,
    pub amount: f64,
    pub confidence: String,
    pub needs_owner: bool,
    pub question: Option<String>,
    pub ruling: Option<String>,
}

#[spacetimedb::table(accessor = trail_event, public)]
pub struct TrailEvent {
    #[primary_key]
    #[auto_inc]
    pub id: u64,
    #[index(btree)]
    pub audit_key: String,
    pub agent: String,
    pub action: String,
    pub detail: String,
    pub at: Timestamp,
}

#[spacetimedb::reducer]
pub fn open_audit(ctx: &ReducerContext, key: String, tenant: String, year: u32) {
    // Re-running an audit replaces its findings and trail, keeping the owner's rulings.
    for f in ctx.db.finding().audit_key().filter(&key) {
        if f.ruling.is_none() {
            ctx.db.finding().id().delete(f.id);
        }
    }
    for e in ctx.db.trail_event().audit_key().filter(&key) {
        ctx.db.trail_event().id().delete(e.id);
    }
    ctx.db.audit().key().delete(&key);
    ctx.db.audit().insert(Audit { key, tenant, year, landlord_says_due: 0.0, actually_due: 0.0,
        status: "running".into(), opened_at: ctx.timestamp });
}

#[spacetimedb::reducer]
pub fn log_event(ctx: &ReducerContext, audit_key: String, agent: String, action: String, detail: String) {
    ctx.db.trail_event().insert(TrailEvent { id: 0, audit_key, agent, action, detail, at: ctx.timestamp });
}

#[spacetimedb::reducer]
pub fn add_finding(ctx: &ReducerContext, audit_key: String, rule: String, title: String, amount: f64,
                   confidence: String, needs_owner: bool, question: Option<String>) {
    let ruling = ctx.db.finding().by_audit_rule().filter((&audit_key, &rule)).find_map(|f| f.ruling);
    for f in ctx.db.finding().by_audit_rule().filter((&audit_key, &rule)) {
        ctx.db.finding().id().delete(f.id);
    }
    ctx.db.finding().insert(Finding { id: 0, audit_key, rule, title, amount, confidence, needs_owner, question, ruling });
}

#[spacetimedb::reducer]
pub fn close_audit(ctx: &ReducerContext, key: String, landlord_says_due: f64, actually_due: f64, status: String) -> Result<(), String> {
    let a = ctx.db.audit().key().find(&key).ok_or("no such audit")?;
    ctx.db.audit().key().update(Audit { landlord_says_due, actually_due, status, ..a });
    Ok(())
}

/// The owner's answer to a finding the agent would not decide on its own.
#[spacetimedb::reducer]
pub fn rule(ctx: &ReducerContext, audit_key: String, rule: String, ruling: String) -> Result<(), String> {
    let f = ctx.db.finding().by_audit_rule().filter((&audit_key, &rule)).next()
        .ok_or("no such finding")?;
    ctx.db.finding().id().update(Finding { ruling: Some(ruling), ..f });
    Ok(())
}
