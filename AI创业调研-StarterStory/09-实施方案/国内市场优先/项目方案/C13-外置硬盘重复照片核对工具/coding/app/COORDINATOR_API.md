# Task 4 integration contract

This document describes production core interfaces available to Tasks 5 and 6. It does not claim those tasks or their UI are implemented.

## Construction and lifetime

Create `TaskStore(url:)` on MainActor, then `ObservationStore(url:)` using the same application database URL. Pass both to `ScanCoordinator(store:taskStore:)`. The coordinator validates the task ID through TaskStore. Keep one coordinator for the application UI. A shared private limiter also enforces at most two active reads and one per physical mount across coordinator instances.

Never put the database inside a scan root. `start` and `retry` reject that layout, including canonical symlink aliases. Production source access uses read-only security-scoped bookmarks and the existing pinned, read-only FileVerifier descriptors. The environment adapter exists for deterministic authorization, identity and read-failure tests; application code uses `.live`.

## Starting, cancelling and resuming

- `try await start(task:roots:)` acquires security-scoped leases, identifies physical volumes, creates a new revision, enumerates ordinary regular files, checkpoints pending records, and reads to stable complete SHA-256 results. It returns after all admitted work terminates. Start it in a Swift `Task` to keep controls responsive.
- `await cancel(task:)` signals the token promptly; the running start/retry call drains active reads, discards incomplete digests, persists cancellation on incomplete records, and returns. Swift task cancellation signals the same token. No concurrent second run for the same task is accepted.
- `try await retry(task:paths:)` restores saved bookmarks and validates UUID, filesystem and mount-root identity before changing observations. Missing UUID, UUID collisions across mounts, changed identities, moved/invalid bookmarks and denied/stale grants fail closed. The original observations and their timestamps survive authorization failure.
- Empty `paths` resumes all incomplete observations. If enumeration was interrupted, it re-enumerates authorized roots and adds only not-yet-recorded paths within the remaining hard quota. Existing completed records are not rehashed or claimed to be currently online. Explicit nonempty `paths` only retries those paths and rejects paths absent from the current task/revision.
- Explicit retries that include completed evidence first atomically fork the visible snapshot to a new revision. A failed recheck can therefore become unknown in the current revision while the old complete evidence remains available by its previous revision. Unselected records retain their checked times, digests and evidence.
- Each file retry opens a new descriptor and hashes from byte zero. Partial digests are never persisted or resumed.
- `start` always creates a fresh revision. This is the supported way to choose fresh directories/re-authorize a failed saved bookmark; old revisions remain available for history. It does not silently transfer old volume identity to a newly selected disk.

## Data for reports and UI

`try await observations(task:)` and `ObservationStore.observations(task:revision:)` return full `ScanObservation` values, including task UUID, absolute canonical path, revision, authorized root URL/bookmark, volume UUID/filesystem/mount root, algorithm version, complete flag, evidence, SHA-256 digest, before/after descriptor stamps, original checked time and failure text. A `FileStamp` holds exact size, modification date and descriptor identity. Derive relative paths from `root.url` if desired. Root bookmarks contain authorization data; do not include bookmark bytes in public report exports.

`ObservationStore.checkpoint(task:)` returns the latest revision's authorized roots, excluded roots, file-limit flag, enumeration errors/completeness and cancellation state. Old observations can be requested using an explicit revision; latest is the default. The `record(task:path:complete:evidence:)` compatibility API stores completion metadata at revision 0; it does not fabricate digest evidence and those legacy rows cannot join a digest group.

`try await progress(task:)` returns discovered/completed/failed/pending counts, running/cancelled status, enumeration completeness, excluded root paths, file-limit status and errors. Counts are based on committed SQLite records, never speculative read results. During enumeration `running` is true and discovery is not yet finalized. A cancelled/failed/quota-limited enumeration must not be presented as full scope coverage. Show admitted counts and `excludedRoots`/`fileLimitReached` prominently. Package directories (including directly selected Photos libraries) and symbolic links are outside the ordinary-file scan scope. Enumeration errors include the failing path.

The hard upper limits are 4 physical volumes and 100,000 admitted files per revision. Multiple roots on the same physical mount share one reader and one volume quota slot. `ScanLimits` may lower limits for controlled runs/tests but cannot raise them. Admission stops at the file boundary; the unenumerated remainder is explicitly represented by the coverage flag rather than an invented exact count.

`try await groups(task:)` derives groups of at least two nonempty, completed files with stable stamps and the same full SHA-256 digest under `sha256-full-v1`. It returns `.hashMatch`; a single completed digest is not itself duplicate evidence. Empty files remain individually identifiable through `isEmptyFile`, and incomplete records stay unknown. No coordinator path promotes hash matches to `.byteEqual`. A future explicit byte-comparison workflow must use the existing FileVerifier checked comparison and persist its separate evidence correctly.

`ScanObservation.currentlyOnline` is deliberately conservative and always false: persisted records are historical observations, including after restart/backup restore. `checkedAt` means last successful full check, not current presence. This core does not expose a durable online flag; a later UI wishing to assert present availability needs a separate live authorization/identity/presence check. Do not infer current online state from `complete` or `checkedAt`.

## SQLite and errors

Tables are `task` (TaskStore), `scan_checkpoint` (task_id, scan_revision, JSON payload) and `observation` (task_id, path, scan_revision, complete, JSON payload). Observation primary key is `(task_id,path,scan_revision)`. JSON payloads are Codable `ScanCheckpoint` and `ScanObservation`, using Foundation's default JSON Date/Data encodings. This task adds no `PRAGMA user_version` migration; Task 5 must define and validate its backup schema explicitly instead of inferring compatibility from a filename.

All observation writes use a serialized SQLite connection and `BEGIN IMMEDIATE`/`COMMIT`; checked digest, both stamps, checked time, complete state and failure transition are written together. Invalid/partial digests are rejected. A `fork` writes its checkpoint and copied rows in the same transaction. SQLite errors propagate to the caller and progress exposes run errors; the UI must show an error rather than success. Scope, identity and read errors are retained as unknown records with failure text where a file was admitted. Authorization failure before admission throws without replacing historical results. The read lease closes after the complete operation. Snapshot/export/restore behavior is intentionally left to Task 5.

## Verification entry points

`./scripts/build.sh` builds the real strict-Swift-6 app and four standalone regression binaries. `build/bin/resume-regression` runs the exact production core, including the 100,001-real-file quota case; `--quick` omits only that capacity case. `TestSupport/ResumeScenarios.swift` is shared by standalone and the Xcode ResumeTests target. Xcode/XCTest execution still requires full Xcode, which this machine lacks.

## Cross-mount boundary (Task 4 review fix 1)

Authorization of a directory does not automatically authorize other volumes mounted beneath it. Enumeration checks each ordinary file/subdirectory's volume identity and actual filesystem device, rejects crossings, and skips crossing-directory descendants. The open wrapper checks the **same pinned descriptor** supplied to FileVerifier before the first data read, then verifies the checked result and path volume again before commit. A mount/device replacement cannot gain data-read permission from a stale enumeration result.

`ScanCheckpoint.excludedRoots` and `ScanProgress.excludedRoots` now contain canonical excluded scope paths: these may be quota-excluded roots, crossed mount subtrees, or individual files excluded during the read phase. They are not exclusively disk-quota entries. Reasons are present in `enumerationFailures`/`progress.failures`; a read-time exclusion also leaves its admitted observation unknown with failure text. All exclusions force `enumerationFinished == false` and survive reopen. Reports and UI must display these paths as incomplete coverage, even if every admitted same-volume file completed. Users may explicitly select a child volume as a separate root within the normal four-volume limit; the parent scan will continue to exclude that crossing.
