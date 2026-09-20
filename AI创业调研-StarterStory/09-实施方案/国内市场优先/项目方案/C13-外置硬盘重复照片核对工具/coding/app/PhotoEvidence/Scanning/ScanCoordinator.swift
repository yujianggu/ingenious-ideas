import Foundation
import Darwin

enum ScanError: Error, Equatable { case alreadyRunning, missingCheckpoint, volumeChanged, invalidRetryPath, databaseInsideSource, outsideAuthorizedVolume }
struct ScanLimits: Sendable {
    let volumes: Int
    let files: Int
    init(volumes: Int = 4, files: Int = 100_000) { self.volumes = min(4, max(1, volumes)); self.files = min(100_000, max(1, files)) }
}
final class ScanCancellation: @unchecked Sendable {
    private let lock = NSLock()
    private var value = false
    var isCancelled: Bool { lock.withLock { value } }
    func cancel() { lock.withLock { value = true } }
}
final class ScanLease: @unchecked Sendable {
    let root: ScanRoot
    private let lock = NSLock()
    private var stop: (@Sendable () -> Void)?
    init(root: ScanRoot, stop: @escaping @Sendable () -> Void = {}) { self.root = root; self.stop = stop }
    func close() { let action = lock.withLock { let old = stop; stop = nil; return old }; action?() }
    deinit { close() }
}
struct ScanEnvironment: Sendable {
    let access: @Sendable (URL, Data?) throws -> ScanLease
    let identity: @Sendable (URL) throws -> VolumeIdentity
    let io: FileVerifierIO
    static let live = ScanEnvironment(access: { url, bookmark in
        let data = try bookmark ?? url.bookmarkData(options: [.withSecurityScope, .securityScopeAllowOnlyReadAccess], includingResourceValuesForKeys: nil, relativeTo: nil)
        var stale = false
        let restored = try URL(resolvingBookmarkData: data, options: .withSecurityScope, relativeTo: nil, bookmarkDataIsStale: &stale)
        guard !stale else { throw ScopeError.staleBookmark }
        guard restored.standardizedFileURL == url.standardizedFileURL else { throw ScopeError.invalidBookmark }
        guard restored.startAccessingSecurityScopedResource() else { throw ScopeError.accessDenied }
        do {
            let root = ScanRoot(url: restored, bookmark: data, volume: try liveIdentity(restored))
            return ScanLease(root: root, stop: { restored.stopAccessingSecurityScopedResource() })
        } catch { restored.stopAccessingSecurityScopedResource(); throw error }
    }, identity: liveIdentity, io: .live)
    static func liveIdentity(_ url: URL) throws -> VolumeIdentity {
        let values = try url.resourceValues(forKeys: [.volumeUUIDStringKey, .volumeURLKey, .isDirectoryKey])
        guard let mount = values.volume else { throw FileVerificationError.unavailable }
        var info = statfs()
        guard statfs(url.path, &info) == 0 else { throw FileVerificationError.unavailable }
        let fs = withUnsafeBytes(of: info.f_fstypename) { bytes in String(cString: bytes.baseAddress!.assumingMemoryBound(to: CChar.self)) }
        return VolumeIdentity(uuid: values.volumeUUIDString, fileSystem: fs, root: mount.standardizedFileURL)
    }
}
struct ScanProgress: Sendable {
    var discovered = 0
    var completed = 0
    var failed = 0
    var pending = 0
    var running = false
    var cancelled = false
    var enumerationFinished = false
    var fileLimitReached = false
    var excludedRoots: [String] = []
    var failures: [String] = []
}
struct EvidenceGroup: Sendable {
    let digest: Data
    let evidence: Evidence
    let observations: [ScanObservation]
}
private actor ScanReadLimiter {
    static let shared = ScanReadLimiter()
    private var busy: Set<String> = []
    private var waiting: [(String, CheckedContinuation<Void, Never>)] = []
    func acquire(_ disk: String) async {
        if busy.count < 2 && !busy.contains(disk) { busy.insert(disk); return }
        await withCheckedContinuation { waiting.append((disk, $0)) }
    }
    func release(_ disk: String) {
        busy.remove(disk)
        while busy.count < 2, let i = waiting.firstIndex(where: { !busy.contains($0.0) }) {
            let next = waiting.remove(at: i); busy.insert(next.0); next.1.resume()
        }
    }
}
private struct EnumerationResult: Sendable {
    var observations: [ScanObservation] = []
    var failures: [String] = []
    var limitReached = false
    var excludedPaths: [String] = []
}
actor ScanCoordinator {
    private let store: ObservationStore
    private let taskStore: TaskStore
    private let environment: ScanEnvironment
    private let limits: ScanLimits
    private var running: [UUID: ScanCancellation] = [:]
    private var sessionFailures: [UUID: String] = [:]
    init(store: ObservationStore, taskStore: TaskStore, environment: ScanEnvironment = .live, limits: ScanLimits = ScanLimits()) {
        self.store = store; self.taskStore = taskStore; self.environment = environment; self.limits = limits
    }

    /// A new scan creates a new revision. Resume/retry uses the existing revision.
    func start(task: UUID, roots: [URL]) async throws {
        guard running[task] == nil else { throw ScanError.alreadyRunning }
        let token = ScanCancellation(); running[task] = token
        defer { running[task] = nil }
        try await withTaskCancellationHandler {
            do {
                try await validateTask(task)
                var leases: [ScanLease] = []
                var devices: [String: UInt64] = [:]
                defer { leases.forEach { $0.close() } }
                var seen: Set<String> = [], disks: Set<String> = [], excluded: [String] = []
                for url in roots {
                    if token.isCancelled { break }
                    let canonical = url.standardizedFileURL.resolvingSymlinksInPath()
                    guard seen.insert(canonical.path).inserted else { continue }
                    try validateDatabaseOutside(canonical)
                    let lease = try environment.access(canonical, nil)
                    let disk = Self.diskKey(lease.root.volume)
                    if !disks.contains(disk) && disks.count >= limits.volumes {
                        excluded.append(canonical.path); lease.close(); continue
                    }
                    disks.insert(disk); leases.append(lease)
                    devices[lease.root.url.path] = try Self.device(try FileVerifierIO.live.stamp(lease.root.url))
                }
                var checkpoint = try store.createCheckpoint(task: task, roots: leases.map(\.root), excluded: excluded)
                let scanRoots = checkpoint.roots, revision = checkpoint.revision, limit = limits.files
                let scanEnvironment = environment, scanDevices = devices
                let enumerated = await Task.detached {
                    Self.enumerate(task: task, revision: revision, roots: scanRoots, limit: limit, token: token, environment: scanEnvironment, devices: scanDevices)
                }.value
                try store.save(enumerated.observations)
                checkpoint.fileLimitReached = enumerated.limitReached
                checkpoint.enumerationFailures = enumerated.failures
                checkpoint.excludedRoots = Array(Set(checkpoint.excludedRoots + enumerated.excludedPaths)).sorted()
                checkpoint.enumerationFinished = !token.isCancelled && enumerated.failures.isEmpty && !enumerated.limitReached && checkpoint.excludedRoots.isEmpty
                checkpoint.cancelled = token.isCancelled
                try store.save(checkpoint: checkpoint)
                let readExclusions = try await run(task: task, observations: enumerated.observations, token: token, devices: devices)
                Self.addReadExclusions(readExclusions, to: &checkpoint)
                checkpoint.cancelled = token.isCancelled
                try store.save(checkpoint: checkpoint)
                sessionFailures[task] = nil
            } catch { sessionFailures[task] = String(describing: error); throw error }
        } onCancel: { token.cancel() }
    }

    func cancel(task: UUID) { running[task]?.cancel() }

    /// Restores every required bookmark and verifies identities before changing any observation.
    /// Empty paths resumes all incomplete observations; completed checkpoints are not rehashed.
    func retry(task: UUID, paths: [String]) async throws {
        guard running[task] == nil else { throw ScanError.alreadyRunning }
        let token = ScanCancellation(); running[task] = token
        defer { running[task] = nil }
        try await withTaskCancellationHandler {
            do {
                try await validateTask(task)
                guard var checkpoint = try store.checkpoint(task: task) else { throw ScanError.missingCheckpoint }
                var all = try store.observations(task: task)
                let requested = Set(paths)
                guard requested.isSubset(of: Set(all.map(\.path))) else { throw ScanError.invalidRetryPath }
                var selected = all.filter { requested.isEmpty ? !$0.complete : requested.contains($0.path) }
                let needsEnumeration = requested.isEmpty && !checkpoint.enumerationFinished && !checkpoint.fileLimitReached
                var leases: [ScanLease] = []
                var devices: [String: UInt64] = [:]
                defer { leases.forEach { $0.close() } }
                let rootPaths = needsEnumeration ? Set(checkpoint.roots.map { $0.url.path }) : Set(selected.compactMap { $0.root?.url.path })
                for root in checkpoint.roots where rootPaths.contains(root.url.path) {
                    try validateDatabaseOutside(root.url)
                    let lease = try environment.access(root.url, root.bookmark)
                    let collision = checkpoint.roots.contains { other in other.volume.uuid == root.volume.uuid && other.volume.root != root.volume.root }
                    guard ScopeResolver.sameTrustedVolume(root.volume, lease.root.volume, collision: collision),
                          lease.root.url.standardizedFileURL == root.url.standardizedFileURL else {
                        lease.close(); throw ScanError.volumeChanged
                    }
                    leases.append(lease)
                    devices[lease.root.url.path] = try Self.device(try FileVerifierIO.live.stamp(lease.root.url))
                }
                if needsEnumeration {
                    let roots = checkpoint.roots, revision = checkpoint.revision, limit = limits.files
                    let scanEnvironment = environment, scanDevices = devices
                    let enumerated = await Task.detached {
                        Self.enumerate(task: task, revision: revision, roots: roots, limit: limit, token: token, environment: scanEnvironment, devices: scanDevices)
                    }.value
                    let existing = Set(all.map(\.path))
                    let additions = enumerated.observations.filter { !existing.contains($0.path) }
                    let capacity = max(0, limits.files - all.count)
                    let accepted = Array(additions.prefix(capacity))
                    try store.save(accepted)
                    all += accepted
                    selected = all.filter { !$0.complete }
                    checkpoint.fileLimitReached = enumerated.limitReached || additions.count > capacity
                    checkpoint.enumerationFailures = enumerated.failures
                    checkpoint.excludedRoots = Array(Set(checkpoint.excludedRoots + enumerated.excludedPaths)).sorted()
                    checkpoint.enumerationFinished = !token.isCancelled && enumerated.failures.isEmpty && !checkpoint.fileLimitReached && checkpoint.excludedRoots.isEmpty
                    try store.save(checkpoint: checkpoint)
                }
                guard selected.allSatisfy({ observation in leases.contains { $0.root.url == observation.root?.url } }) else { throw ScopeError.invalidBookmark }
                if selected.contains(where: \.complete) {
                    checkpoint = try store.fork(checkpoint: checkpoint, observations: all)
                    for i in selected.indices { selected[i].revision = checkpoint.revision }
                }
                let readExclusions = try await run(task: task, observations: selected, token: token, devices: devices)
                Self.addReadExclusions(readExclusions, to: &checkpoint)
                checkpoint.cancelled = token.isCancelled
                try store.save(checkpoint: checkpoint)
                sessionFailures[task] = nil
            } catch { sessionFailures[task] = String(describing: error); throw error }
        } onCancel: { token.cancel() }
    }

    func progress(task: UUID) throws -> ScanProgress {
        let observations = try store.observations(task: task)
        let checkpoint = try store.checkpoint(task: task)
        var progress = ScanProgress()
        progress.discovered = observations.count
        progress.completed = observations.filter(\.complete).count
        progress.failed = observations.filter { !$0.complete && $0.failure != nil }.count
        progress.pending = observations.count - progress.completed - progress.failed
        progress.running = running[task] != nil
        progress.cancelled = checkpoint?.cancelled == true || running[task]?.isCancelled == true
        progress.enumerationFinished = checkpoint?.enumerationFinished ?? false
        progress.fileLimitReached = checkpoint?.fileLimitReached ?? false
        progress.excludedRoots = checkpoint?.excludedRoots ?? []
        progress.failures = checkpoint?.enumerationFailures ?? []
        if let failure = sessionFailures[task] { progress.failures.append(failure) }
        return progress
    }
    func observations(task: UUID) throws -> [ScanObservation] { try store.observations(task: task) }
    /// Group membership requires complete, stable, full digests. Empty files stay separate.
    func groups(task: UUID) throws -> [EvidenceGroup] {
        let valid = try observations(task: task).filter { $0.complete && $0.algorithm == "sha256-full-v1" && !$0.isEmptyFile && $0.digest?.count == 32 && $0.before == $0.after && $0.before != nil }
        return Dictionary(grouping: valid, by: { $0.digest! }).filter { $0.value.count > 1 }.map {
            EvidenceGroup(digest: $0.key, evidence: .hashMatch, observations: $0.value)
        }.sorted { $0.observations[0].path < $1.observations[0].path }
    }
    private func validateTask(_ task: UUID) async throws {
        guard try await taskStore.tasks().contains(where: { $0.id == task }) else { throw StoreError.taskNotFound(task) }
    }
    private func validateDatabaseOutside(_ root: URL) throws {
        if (try? ScopeResolver.validateChild(root: root, file: store.url)) != nil { throw ScanError.databaseInsideSource }
    }
    nonisolated private static func diskKey(_ volume: VolumeIdentity) -> String {
        // Physical mount identity also serializes roots without UUIDs during a fresh scan.
        volume.root.standardizedFileURL.path
    }
    nonisolated private static func device(_ stamp: FileStamp) throws -> UInt64 {
        guard let component = stamp.identity.split(separator: ":").first, let value = UInt64(component) else { throw ScanError.outsideAuthorizedVolume }
        return value
    }
    nonisolated private static func addReadExclusions(_ paths: [String], to checkpoint: inout ScanCheckpoint) {
        guard !paths.isEmpty else { return }
        checkpoint.excludedRoots = Array(Set(checkpoint.excludedRoots + paths)).sorted()
        checkpoint.enumerationFailures += paths.map { $0 + ": opened file is outside the authorized volume" }
        checkpoint.enumerationFinished = false
    }
    private func run(task: UUID, observations: [ScanObservation], token: ScanCancellation, devices: [String: UInt64]) async throws -> [String] {
        var exclusions: [String] = []
        let byDisk = Dictionary(grouping: observations, by: { Self.diskKey($0.root!.volume) })
        do {
            try await withThrowingTaskGroup(of: [String].self) { group in
                for (disk, entries) in byDisk {
                    group.addTask {
                        var excluded: [String] = []
                        for entry in entries {
                            if token.isCancelled { break }
                            guard let root = entry.root, let device = devices[root.url.path] else { throw ScopeError.invalidBookmark }
                            if try await self.verify(entry, disk: disk, token: token, device: device) { excluded.append(entry.path) }
                        }
                        return excluded
                    }
                }
                for try await paths in group { exclusions += paths }
            }
        } catch { token.cancel(); throw error }
        if token.isCancelled {
            var pending = try store.observations(task: task).filter { !$0.complete }
            for i in pending.indices { pending[i].failure = "cancelled"; pending[i].digest = nil; pending[i].evidence = .unknown }
            try store.save(pending)
        }
        return exclusions
    }
    private func verify(_ observation: ScanObservation, disk: String, token: ScanCancellation, device: UInt64) async throws -> Bool {
        var excluded = false
        await ScanReadLimiter.shared.acquire(disk)
        // The permit is released after commit, so a cancelled reader cannot race the next read.
        do {
            if !token.isCancelled {
                let environment = self.environment
                let result: Result<CheckedDigest, Error> = await Task.detached {
                    do {
                        guard let root = observation.root else { throw ScopeError.invalidBookmark }
                        let beforeVolume = try environment.identity(root.url)
                        guard beforeVolume == root.volume else { throw ScanError.volumeChanged }
                        let source = URL(fileURLWithPath: observation.path)
                        guard try environment.identity(source) == root.volume,
                              try Self.device(FileVerifierIO.live.stamp(source)) == device else { throw ScanError.outsideAuthorizedVolume }
                        let base = environment.io
                        let io = FileVerifierIO(stamp: base.stamp, open: { url in
                            let session = try base.open(url)
                            do {
                                // Inspect the actual pinned descriptor before FileVerifier can read.
                                // A path-only stat here would reintroduce a mount replacement race.
                                guard let stamp = session.stamp, let location = session.location,
                                      try Self.device(stamp()) == device,
                                      try environment.identity(location) == root.volume else { throw ScanError.outsideAuthorizedVolume }
                                return session
                            } catch { session.close(); throw error }
                        }, isCancelled: { token.isCancelled || base.isCancelled() })
                        let result = try FileVerifier(authorizedRoots: [root.url], io: io).digestChecked(url: URL(fileURLWithPath: observation.path))
                        guard try environment.identity(root.url) == root.volume else { throw ScanError.volumeChanged }
                        guard try environment.identity(source) == root.volume,
                              try Self.device(result.after) == device else { throw ScanError.outsideAuthorizedVolume }
                        return .success(result)
                    } catch { return .failure(error) }
                }.value
                var updated = observation
                updated.complete = false; updated.digest = nil; updated.evidence = .unknown
                updated.before = nil; updated.after = nil; updated.checkedAt = nil
                if token.isCancelled { updated.failure = "cancelled" }
                else {
                    switch result {
                    case .success(let digest):
                        updated.complete = true; updated.digest = digest.value
                        updated.before = digest.before; updated.after = digest.after
                        updated.checkedAt = Date(); updated.failure = nil
                        // A single digest is not evidence of a duplicate. groups() derives matches.
                    case .failure(let error):
                        updated.failure = String(describing: error)
                        excluded = error as? ScanError == .outsideAuthorizedVolume
                    }
                }
                try store.save([updated])
            }
        } catch {
            token.cancel(); await ScanReadLimiter.shared.release(disk); throw error
        }
        await ScanReadLimiter.shared.release(disk)
        return excluded
    }
    nonisolated private static func enumerate(task: UUID, revision: Int, roots: [ScanRoot], limit: Int, token: ScanCancellation, environment: ScanEnvironment, devices: [String: UInt64]) -> EnumerationResult {
        var result = EnumerationResult(), seen: Set<String> = []
        for root in roots {
            if token.isCancelled { break }
            var rootFailure: String?
            do {
                if try root.url.resourceValues(forKeys: [.isPackageKey]).isPackage == true || root.url.pathExtension.lowercased() == "photoslibrary" {
                    result.failures.append(root.url.path + ": package directories are outside supported scope")
                    continue
                }
            } catch { result.failures.append("\(root.url.path): \(error)"); continue }
            guard let enumerator = FileManager.default.enumerator(at: root.url, includingPropertiesForKeys: [.isRegularFileKey, .isSymbolicLinkKey, .isPackageKey], options: [.skipsPackageDescendants], errorHandler: { url, error in
                rootFailure = "\(url.path): \(error)"; return false
            }) else { result.failures.append(root.url.path + ": unavailable"); continue }
            for case let url as URL in enumerator {
                if token.isCancelled { break }
                do {
                    let properties = try url.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .isPackageKey])
                    if properties.isPackage == true { enumerator.skipDescendants(); continue }
                    guard properties.isSymbolicLink != true else { enumerator.skipDescendants(); continue }
                    let canonical = try ScopeResolver.validateChild(root: root.url, file: url)
                    guard try environment.identity(canonical) == root.volume,
                          let expectedDevice = devices[root.url.path],
                          try Self.device(FileVerifierIO.live.stamp(url)) == expectedDevice else {
                        enumerator.skipDescendants()
                        result.excludedPaths.append(canonical.path)
                        result.failures.append(canonical.path + ": outside the authorized volume")
                        continue
                    }
                    guard properties.isRegularFile == true else { continue }
                    guard seen.insert(canonical.path).inserted else { continue }
                    if result.observations.count == limit { result.limitReached = true; return result }
                    result.observations.append(ScanObservation(taskID: task, path: canonical.path, revision: revision, root: root))
                } catch { result.failures.append("\(url.path): \(error)") }
            }
            if let rootFailure { result.failures.append(rootFailure) }
        }
        return result
    }
}
