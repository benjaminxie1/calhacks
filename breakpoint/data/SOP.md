# Meridian Securities — Reconciliation Operations SOP (rev. 2026-03)

Owner: Dana Ortiz (Operations Lead). Approver of record: Sam Lee (Controller).

## 1. Daily cycle
1. Ingest the OMS, executing-broker (Northgate Execution) and custodian (Harbor Clearing) files.
2. Run transaction recon (orders ↔ executions ↔ ledger ↔ custodian settlements), then end-of-day position and cash recon.
3. Settlement is T+1. A trade that is booked today and settles tomorrow is a timing difference, not a break.
4. Price/principal differences of $0.01 or less per trade are rounding and are cleared with a note.

## 2. Who is right
- **Quantity traded:** the executing broker's fills. The OMS says what was asked for; fills say what happened.
- **Quantity held / settled:** the custodian statement.
- **Commission:** the client fee schedule in force on the trade date (watch effective dates after any repricing).
- **Corporate actions:** the custodian plus the corporate-action feed.
- **Money in and out:** the bank.

## 3. Known breaks and the fix
### 3.1 Partial fill booked as a full fill
The booking system books the full order quantity although the broker only filled part of it.
Reverse the unfilled shares, the cash and the extra commission so the booking matches the fills. Tell Client Service (Alex Kim) so the client sees a corrected confirm.

### 3.2 Commission charged at the wrong tier / stale schedule
The commission charged matches the *previous* schedule version even though a new version was in force on the trade date.
Refund the difference with an adjusting entry and send the client a notice.

### 3.3 Duplicate execution report
Northgate occasionally re-sends a report (PossDup flag). The booking system books it a second time; the custodian settles once.
Confirm with the broker desk first, then reverse the duplicate posting exactly.

### 3.4 Dividend not credited
The custodian credits a cash dividend but our corporate-action job missed the account.
Post the dividend: rate × shares held on the record date (custodian position).

## 4. Controls
- No one approves their own entry. Entries over $500, all client money movements and all new procedures need the Controller.
- Never edit a closed month. Months close on the 3rd business day; late fixes post in the open month with a reference to the original entry.
- If none of the above fits, stop and ask the Operations Lead. Do not improvise.
