# C14 — Travel Dossier

English UI configuration: `shared/products/c14.json`. Domain: `backend/app/products/c14.py`.

A private dossier contains a destination, default IANA timezone, notes, chronological itinerary entries, import preview, and original attachments. Account isolation, revisions, local offline caches and original-file downloads are supplied by the shared application. The domain makes no email parsing, OCR, booking-provider or AI extraction claims.

## Workflow

1. Create a dossier with a title and IANA timezone, such as `Asia/Tokyo`.
2. Add flight/hotel/train/car/activity/other entries. Both dates must be ISO 8601 with explicit UTC offsets, for example `2026-10-01T09:00:00+09:00`. The named timezone must match both offsets. End cannot precede start.
3. Alternatively use **Preview calendar or CSV import**, inspect accepted entries and rejected rows, then **Add reviewed entries**. Nothing is added during preview. Repeat imports skip duplicates identified by title, absolute start/end and location.
4. Attach the actual booking document. Accepted types are PDF, PNG, JPEG, UTF-8 TXT and ICS, with matching extension/type and basic byte-signature validation. The limits are 500 KiB per file, 20 files per dossier and 500 itinerary entries. This is type validation, not a malware scanner or guarantee that a PDF/image is fully renderable.
5. Export readable TXT, structured CSV, UTC ICS, or a ZIP containing the itinerary plus every original file, unchanged. JSON backup is provided by the common API.

## Import contract

CSV headers: `title,kind,start,end,timezone,location,reference,notes`. `title,start,end,timezone` are required headers; other columns are optional. Unknown columns are ignored. Missing/extra row values, invalid dates and duplicates are rejected with reasons. Formula-like text is apostrophe-escaped on CSV export and decoded by this importer; already-apostrophe-prefixed text is escaped again so roundtrips preserve it.

Calendar input must be a complete VCALENDAR document containing VEVENT records with SUMMARY, DTSTART and DTEND. UTC dates and standard IANA TZID dates are supported, including folded UTF-8 text and basic calendar escaping. Floating dates, all-day dates, recurring events, recurrence exceptions, DURATION-only events, nested event components, cancelled events (including calendar-level `METHOD:CANCEL`) and ambiguous/nonexistent daylight-saving times are rejected for manual entry. Calendar files using custom, non-IANA timezone identifiers are rejected. The importer does not fetch external resources, expand recurrences or infer missing dates. The import limit is 200 events and 500 KiB of text. Exported ICS uses UTC for compatibility; the CSV/TXT retain the original timezone and offsets.

## Actions and data

- `add-entry {title,kind,start,end,timezone,location,reference,notes}` and `remove-entry {entryId}`.
- `preview-import {format: "ics"|"csv", content}` writes `importPreview.entries` and `importPreview.rejected`; `commit-import {}` adds the accepted entries.
- `add-attachment {file: {name,contentType,base64}}` and `remove-attachment {attachmentId}`.
- Attachment schema: `{id,name,contentType,base64,size}`. ZIP filenames are generated from safe stored names and unique attachment IDs.

All actions return a copied record. Externally supplied metadata does not override server identity, ownership or revision. Manual corrections can be made by removing and re-adding an entry. Editing requires connectivity; offline access uses the shared device cache. Document summaries are never presented as original ticket files.

## Verification

`cd backend && .venv/bin/python -m pytest tests/test_domain.py -q` covers explicit timezone offsets, invalid zones, preview/commit and duplicate handling, recurrence, cancellation-method and daylight-saving rejection, folded calendar text roundtrip, malformed CSV rows, exact attachment bytes, ZIP extraction, unsafe filenames, signature rejection, and file-size limits.
