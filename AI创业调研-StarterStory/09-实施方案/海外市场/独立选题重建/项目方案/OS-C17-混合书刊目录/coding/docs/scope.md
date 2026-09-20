# C17 — Shelf Catalog

English UI configuration: `shared/products/c17.json`. Domain: `backend/app/products/c17.py`.

A private mixed collection stores books, comics and magazines with title, optional positive integer volume/issue, optional ISBN, shelf/box location and notes. Entries sort by normalized title, type, numeric volume and numeric issue; issue 2 sorts before issue 10. The collection limit is 5000 entries.

## Workflow

Add publications individually or preview a structured CSV. Inspect accepted and rejected rows before confirming the import. Search by title, ISBN or notes, optionally filter publication type and exact location. Complete valid ISBN queries are normalized, so ISBN-10, its ISBN-13 equivalent, and hyphenated forms find the same entry. Move an entry to a new shelf or update notes. To correct bibliographic identity, remove the entry and re-add it with the corrected fields.

For comics/magazines, **Find missing issues** takes an exact series title, optional volume, and the expected first/last issue. Results show absent integer issues within that user-supplied range, up to 1001 issues. This does not claim knowledge of publisher release dates, special editions, decimal-numbered issues or unpublished issues. Volume/issue values are integers 1–100000. Unnumbered entries leave the field blank.

ISBN-10 and ISBN-13 checksums are validated when supplied. ISBN-10 is stored as the equivalent ISBN-13, so the two forms cannot bypass duplicate detection. ISBN-13 must start with 978 or 979. Hyphens/spaces are accepted; non-ASCII digits are rejected. Checksum validity does not establish that the publication exists. No external metadata lookup or barcode scanner is included.

Duplicates are identified by normalized ISBN, or by normalized type/title/volume/issue. Labels use Unicode normalization, case folding and collapsed whitespace. This models one catalog entry per edition/issue; it does not track multiple physical copies of the exact same edition.

## Import, export and backup

CSV headers: `type,title,volume,issue,isbn,location,notes`. `type` and `title` are required. Type is `book`, `comic` or `magazine`. Omit values for absent volume/issue/ISBN. The importer rejects duplicate headers, malformed rows, invalid quantities/ISBNs and duplicates both within the file and against the collection, with per-row reasons. Unknown columns are ignored. Limits are 1000 rows and 1 MiB per import.

Exports use those same clear field names. Text starting with `=`, `+`, `-`, `@`, tab, carriage return or apostrophe gets an apostrophe prefix to avoid spreadsheet formula execution. The importer reverses this escaping, including existing apostrophes, so this application's exports roundtrip without corrupting catalog text. Numeric volume/issue values remain numeric CSV cells. TXT is available for a readable list.

The shared JSON export is the full collection backup. **Restore collection backup** replaces the current collection with validated entries from a C17 JSON backup; domain entry IDs are regenerated and server ownership/identity/revision fields are retained. Restore is atomic and rejects invalid or duplicate entries. Its input limit is 16 MiB of JSON characters, allowing larger valid collection exports to be read; the shared API still enforces the 12 MiB stored-record limit. Export the current JSON first to preserve it before replacement.

## Actions and result views

- `add-entry {type,title,volume,issue,isbn,location,notes}`.
- `move-entry {entryId,location}`, `update-notes {entryId,notes}`, `remove-entry {entryId}`.
- `query {search,type,location}` writes `queryResults`.
- `find-gaps {title,type,volume,fromIssue,toIssue}` writes `gapQuery` and `gapResults`.
- `preview-import {content}` writes `importPreview.entries` and `.rejected`; `commit-import {}` adds accepted entries.
- `restore-backup {content}` accepts JSON text read from a backup file.

Search/gap views are cleared after catalog changes to avoid stale results. Editing requires connectivity; the shared device cache provides offline reading.

## Verification

`cd backend && .venv/bin/python -m pytest tests/test_domain.py -q` covers numeric ordering, ISBN validation and equivalent ISBN duplicates, canonical title duplicates, search/location filters, issue gaps, CSV preview and roundtrip, formula-safe exports, Unicode numeral and oversized numeric-field rejection, equivalent-ISBN search, JSON restore above 2 MiB, and atomic rejection of invalid backups.
