# EDGAR Retail Lease Finder

Public companies file their store and office leases with the SEC as exhibits. This Actor
searches EDGAR full text for them and pulls out the clauses that decide common area
maintenance (CAM) charges, each with its exact quote:

| Field | What it catches |
| --- | --- |
| `pro_rata_share` | How the tenant's share is defined |
| `cam_cap` | Caps on year-over-year increases in controllable CAM |
| `admin_fee` | Administrative or management fee percentages |
| `capital_exclusion` | Whether capital repairs can be passed through |
| `real_estate_taxes` | Tax pass-through language |
| `audit_right` | The tenant's right to audit the landlord's books |

Used by the CAM audit agent in this repo to test its lease parser on real leases.
It identifies itself to the SEC with the `userAgent` input and stays far below SEC's
10 requests per second limit.
