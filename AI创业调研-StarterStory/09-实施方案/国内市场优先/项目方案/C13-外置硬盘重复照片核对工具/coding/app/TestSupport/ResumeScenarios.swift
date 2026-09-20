import Foundation
#if canImport(PhotoEvidence)
@testable import PhotoEvidence
#endif

enum ResumeFailure: Error { case assertion(String), injected }
func require(_ condition: Bool, _ message: String) throws { if !condition { throw ResumeFailure.assertion(message) } }
final class ScanProbe: @unchecked Sendable {
    private let lock = NSLock()
    var active = 0, peak = 0, opened = 0
    var perDisk: [String: Int] = [:]
    var diskPeak = 0
    var fail = false, deny = false, changed = false
    var delay: UInt32 = 0
    var reads = 0
    var failAfterFirstRead = false
    func update(_ action: (ScanProbe) -> Void) { lock.withLock { action(self) } }
    func read<T>(_ action: (ScanProbe) -> T) -> T { lock.withLock { action(self) } }
}
struct ResumeFixture: Sendable {
    let base: URL
    let store: ObservationStore
    let tasks: TaskStore
    let id: UUID
    @MainActor init() throws {
        base = FileManager.default.temporaryDirectory.resolvingSymlinksInPath().appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: base, withIntermediateDirectories: true)
        store = try ObservationStore(url: base.appendingPathComponent("index.sqlite"))
        tasks = try TaskStore(url: base.appendingPathComponent("index.sqlite"))
        id = try tasks.createTask(name: "test")
    }
    func root(_ name: String, count: Int = 1) throws -> URL {
        let root = base.appendingPathComponent(name)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        for i in 0..<count { try Data("same content".utf8).write(to: root.appendingPathComponent("\(i).jpg")) }
        return root
    }
    func clean() { try? FileManager.default.removeItem(at: base) }
    func environment(_ probe: ScanProbe = ScanProbe(), sameVolume: Bool = false, missingUUID: Bool = false, collision: Bool = false) -> ScanEnvironment {
        let identity: @Sendable (URL) throws -> VolumeIdentity = { url in
            let canonicalBase = self.base.resolvingSymlinksInPath()
            let relative = url.resolvingSymlinksInPath().pathComponents.dropFirst(canonicalBase.pathComponents.count)
            let root = relative.first.map { canonicalBase.appendingPathComponent($0) } ?? canonicalBase
            return VolumeIdentity(uuid: missingUUID ? nil : (probe.read { $0.changed } ? "replacement" : ((sameVolume || collision) ? "disk" : root.lastPathComponent)), fileSystem: "APFS", root: sameVolume ? self.base : root)
        }
        return ScanEnvironment(access: { root, _ in
            if probe.read({ $0.deny }) { throw ScopeError.accessDenied }
            return ScanLease(root: ScanRoot(url: root, bookmark: Data(root.path.utf8), volume: try identity(root)))
        }, identity: identity, io: FileVerifierIO(stamp: FileVerifierIO.live.stamp, open: { url in
            let session = try FileVerifierIO.live.open(url)
            let disk = sameVolume ? "disk" : url.deletingLastPathComponent().lastPathComponent
            probe.update { p in p.opened += 1; p.active += 1; p.peak = max(p.peak, p.active); p.perDisk[disk, default: 0] += 1; p.diskPeak = max(p.diskPeak, p.perDisk[disk]!) }
            var readCount = 0
            return FileReadSession(read: { maximum in
                let delay = probe.read { $0.delay }; if delay > 0 { usleep(delay) }
                readCount += 1; probe.update { $0.reads += 1 }
                if probe.read({ $0.fail || ($0.failAfterFirstRead && readCount > 1) }) { throw FileVerificationError.unavailable }
                return try session.read(maximum)
            }, stamp: session.stamp, location: session.location, close: {
                session.close(); probe.update { p in p.active -= 1; p.perDisk[disk, default: 0] -= 1 }
            })
        }, isCancelled: { false }))
    }
}
enum ResumeScenarios {
    static func scanAndHistory() async throws {
        let f = try await ResumeFixture(); defer { f.clean() }
        let a = try f.root("a", count: 2)
        let c = ScanCoordinator(store: f.store, taskStore: f.tasks, environment: f.environment())
        try await c.start(task: f.id, roots: [a])
        let records = try f.store.observations(task: f.id)
        try require(records.count == 2 && records.allSatisfy { $0.complete && $0.digest?.count == 32 && $0.checkedAt != nil && $0.before == $0.after }, "stable full digests must persist")
        try require(try await c.groups(task: f.id).count == 1, "matching files must group")
        let reopen = try ObservationStore(url: f.store.url)
        try require(try reopen.completed(task: f.id).count == 2, "reopen must preserve checkpoints")
        try require(try reopen.observations(task: f.id).allSatisfy { !$0.currentlyOnline }, "history must be offline")
        let other = try await f.tasks.createTask(name: "test")
        try require(try f.store.completed(task: other).isEmpty, "task isolation")
    }
    static func cancellationAndResume() async throws {
        let f = try await ResumeFixture(); defer { f.clean() }
        let root = try f.root("a", count: 20), p = ScanProbe(); p.update { $0.delay = 20_000 }
        let c = ScanCoordinator(store: f.store, taskStore: f.tasks, environment: f.environment(p))
        let run = Task { try await c.start(task: f.id, roots: [root]) }
        let deadline = Date().addingTimeInterval(10)
        while try f.store.completed(task: f.id).isEmpty {
            try require(Date() < deadline, "scan did not commit first file")
            try await Task.sleep(for: .milliseconds(10))
        }
        await c.cancel(task: f.id); try await run.value
        let before = try f.store.observations(task: f.id)
        try require(before.contains { $0.complete } && before.contains { !$0.complete }, "cancel must separate completed and pending")
        try require(before.filter { !$0.complete }.allSatisfy { $0.digest == nil && $0.evidence == .unknown }, "no half digest")
        let reopened = try ObservationStore(url: f.store.url)
        let c2 = ScanCoordinator(store: reopened, taskStore: f.tasks, environment: f.environment(p))
        try await c2.retry(task: f.id, paths: before.filter { !$0.complete }.map(\.path))
        try require(try reopened.completed(task: f.id).count == 20, "resume completes pending")
        let after = try reopened.observations(task: f.id)
        try require(before.filter(\.complete).allSatisfy { old in after.contains { $0.path == old.path && $0.checkedAt == old.checkedAt } }, "resume preserves completed timestamps")
    }
    static func disconnectAndAuthorization() async throws {
        let f = try await ResumeFixture(); defer { f.clean() }
        let root = try f.root("a", count: 2), p = ScanProbe(); p.update { $0.failAfterFirstRead = true }
        let c = ScanCoordinator(store: f.store, taskStore: f.tasks, environment: f.environment(p))
        try await c.start(task: f.id, roots: [root])
        let before = try f.store.observations(task: f.id)
        try require(before.count == 2 && before.allSatisfy { !$0.complete && $0.digest == nil && $0.failure != nil }, "read failure persists unknown")
        p.update { $0.failAfterFirstRead = false; $0.deny = true }
        do { try await c.retry(task: f.id, paths: before.map(\.path)); throw ResumeFailure.assertion("denied authorization accepted") } catch ScopeError.accessDenied {}
        try require(try f.store.observations(task: f.id) == before, "authorization failure preserves history")
        p.update { $0.deny = false; $0.changed = true }
        do { try await c.retry(task: f.id, paths: before.map(\.path)); throw ResumeFailure.assertion("replacement volume accepted") } catch ScanError.volumeChanged {}
        p.update { $0.changed = false }
        try await c.retry(task: f.id, paths: before.map(\.path))
        try require(try f.store.completed(task: f.id).count == 2, "reconnected reads start from file boundary")
    }
    static func quotas() async throws {
        let f = try await ResumeFixture(); defer { f.clean() }
        let roots = try (0..<5).map { try f.root("disk\($0)", count: 2) }
        let c = ScanCoordinator(store: f.store, taskStore: f.tasks, environment: f.environment(), limits: ScanLimits(files: 3))
        try await c.start(task: f.id, roots: roots)
        let progress = try await c.progress(task: f.id)
        try require(progress.discovered == 3 && progress.fileLimitReached && progress.excludedRoots.count == 1, "quotas must expose covered scope")
    }
    static func concurrency() async throws {
        let f = try await ResumeFixture(); defer { f.clean() }
        let roots = try [f.root("a", count: 3), f.root("b", count: 3)], p = ScanProbe(); p.update { $0.delay = 20_000 }
        let c = ScanCoordinator(store: f.store, taskStore: f.tasks, environment: f.environment(p))
        let other = try await f.tasks.createTask(name: "other")
        async let first: Void = c.start(task: f.id, roots: roots)
        async let second: Void = c.start(task: other, roots: roots)
        _ = try await (first, second)
        try require(p.read { $0.peak == 2 && $0.diskPeak == 1 && $0.opened == 12 }, "global two and per volume one across tasks")
    }
    static func atomicFailure() async throws {
        let f = try await ResumeFixture(); defer { f.clean() }
        let failing = try ObservationStore(url: f.store.url, beforeCommit: { throw ResumeFailure.injected })
        do { try failing.record(task: f.id, path: "a", complete: true, evidence: .hashMatch); throw ResumeFailure.assertion("write failure accepted") } catch ResumeFailure.injected {}
        try require(try f.store.completed(task: f.id).isEmpty, "failed transaction cannot leak completion")
    }
    static func resumeInterruptedEnumeration() async throws {
        let f = try await ResumeFixture(); defer { f.clean() }
        let root = try f.root("a", count: 3)
        let env = f.environment(), lease = try env.access(root, nil)
        let checkpoint = try f.store.createCheckpoint(task: f.id, roots: [lease.root], excluded: [])
        let path = root.appendingPathComponent("0.jpg")
        let digest = try FileVerifier(authorizedRoots: [root]).digestChecked(url: path)
        var completed = ScanObservation(taskID: f.id, path: path.path, revision: checkpoint.revision, root: lease.root)
        completed.complete = true; completed.digest = digest.value; completed.before = digest.before; completed.after = digest.after; completed.checkedAt = Date(timeIntervalSince1970: 42)
        try f.store.save([completed]); lease.close()
        let reopened = try ObservationStore(url: f.store.url)
        let c = ScanCoordinator(store: reopened, taskStore: f.tasks, environment: env)
        try await c.retry(task: f.id, paths: [])
        try require(try reopened.completed(task: f.id).count == 3, "unfinished enumeration must discover remaining files")
        try require(try reopened.observations(task: f.id).first { $0.path == path.path }?.checkedAt == completed.checkedAt, "completed digest checkpoint must remain untouched")
    }
    static func sourceBoundaries() async throws {
        let f = try await ResumeFixture(); defer { f.clean() }
        let root = try f.root("a")
        let library = root.appendingPathComponent("Photos.photoslibrary")
        try FileManager.default.createDirectory(at: library, withIntermediateDirectories: true)
        try Data("private".utf8).write(to: library.appendingPathComponent("photo.jpg"))
        let outside = try f.root("outside")
        try FileManager.default.createSymbolicLink(at: root.appendingPathComponent("link.jpg"), withDestinationURL: outside.appendingPathComponent("0.jpg"))
        let c = ScanCoordinator(store: f.store, taskStore: f.tasks, environment: f.environment())
        try await c.start(task: f.id, roots: [root])
        try require(try f.store.completed(task: f.id) == [root.appendingPathComponent("0.jpg").path], "packages and symlinks excluded")
        do { try await c.start(task: f.id, roots: [f.base]); throw ResumeFailure.assertion("database inside source accepted") } catch ScanError.databaseInsideSource {}
        let other = try await f.tasks.createTask(name: "package")
        try await c.start(task: other, roots: [library])
        try require(try f.store.completed(task: other).isEmpty, "selected photo library must not be scanned")
    }
    static func identityAmbiguity() async throws {
        for missing in [true, false] {
            let f = try await ResumeFixture(); defer { f.clean() }
            let roots = try [f.root("a"), f.root("b")]
            let c = ScanCoordinator(store: f.store, taskStore: f.tasks, environment: f.environment(missingUUID: missing, collision: !missing))
            try await c.start(task: f.id, roots: roots)
            let before = try f.store.observations(task: f.id)
            do { try await c.retry(task: f.id, paths: before.map(\.path)); throw ResumeFailure.assertion("ambiguous identity resumed") } catch ScanError.volumeChanged {}
            try require(try f.store.observations(task: f.id) == before, "identity rejection changes no history")
        }
    }
    static func revisionsAndInvalidDigest() async throws {
        let f = try await ResumeFixture(); defer { f.clean() }
        let root = try f.root("a")
        let c = ScanCoordinator(store: f.store, taskStore: f.tasks, environment: f.environment())
        try await c.start(task: f.id, roots: [root])
        let old = try f.store.observations(task: f.id)
        try Data("new".utf8).write(to: root.appendingPathComponent("0.jpg"))
        try await c.start(task: f.id, roots: [root])
        let current = try f.store.observations(task: f.id)
        try require(current[0].revision > old[0].revision && current[0].digest != old[0].digest, "new revision must hash changed source")
        try require(try f.store.observations(task: f.id, revision: old[0].revision) == old, "historical revision must remain")
        var invalid = current[0]; invalid.digest = Data([1, 2])
        do { try f.store.save([invalid]); throw ResumeFailure.assertion("partial digest accepted") } catch ObservationError.invalidDigest {}
        try require(try f.store.observations(task: f.id) == current, "invalid digest atomic rejection")
        let failing = try ObservationStore(url: f.store.url, beforeCommit: { throw ResumeFailure.injected })
        invalid = current[0]; invalid.complete = false; invalid.digest = nil
        do { try failing.save([invalid]); throw ResumeFailure.assertion("failed digest transition committed") } catch ResumeFailure.injected {}
        try require(try f.store.observations(task: f.id) == current, "digest and status rollback together")
    }
    static func recheckHistoryAndAlgorithm() async throws {
        let f = try await ResumeFixture(); defer { f.clean() }
        let root = try f.root("a", count: 2), p = ScanProbe()
        let c = ScanCoordinator(store: f.store, taskStore: f.tasks, environment: f.environment(p))
        try await c.start(task: f.id, roots: [root])
        let old = try f.store.observations(task: f.id)
        p.update { $0.failAfterFirstRead = true }
        try await c.retry(task: f.id, paths: [old[0].path])
        try require(try f.store.observations(task: f.id, revision: old[0].revision) == old, "failed recheck must retain old evidence in historical revision")
        var current = try f.store.observations(task: f.id)
        try require(current[0].revision > old[0].revision && !current[0].complete && current[1].checkedAt == old[1].checkedAt, "recheck updates only selected record")
        p.update { $0.failAfterFirstRead = false }
        try await c.retry(task: f.id, paths: [old[0].path])
        current = try f.store.observations(task: f.id)
        current[0].algorithm = "future-hash-v2"
        try f.store.save([current[0]])
        try require(try await c.groups(task: f.id).isEmpty, "different algorithm versions cannot group")
    }
    static func nestedVolumeExclusions() async throws {
        let f = try await ResumeFixture(); defer { f.clean() }
        let parent = try f.root("parent")
        let children = (0..<5).map { parent.appendingPathComponent("disk\($0)") }
        for child in children {
            try FileManager.default.createDirectory(at: child, withIntermediateDirectories: true)
            try Data("nested".utf8).write(to: child.appendingPathComponent("photo.jpg"))
        }
        let p = ScanProbe()
        let identity: @Sendable (URL) throws -> VolumeIdentity = { url in
            let mount = children.first { (try? ScopeResolver.validateChild(root: $0, file: url)) != nil } ?? parent
            return VolumeIdentity(uuid: mount.lastPathComponent, fileSystem: "APFS", root: mount)
        }
        let env = ScanEnvironment(access: { url, _ in
            ScanLease(root: ScanRoot(url: url, bookmark: Data(), volume: try identity(url)))
        }, identity: identity, io: FileVerifierIO(stamp: FileVerifierIO.live.stamp, open: { url in
            let session = try FileVerifierIO.live.open(url)
            let disk = try identity(url).root.path
            p.update { $0.opened += 1 }
            return FileReadSession(read: { maximum in
                p.update { $0.perDisk[disk, default: 0] += 1; $0.diskPeak = max($0.diskPeak, $0.perDisk[disk]!) }
                defer { p.update { $0.perDisk[disk, default: 0] -= 1 } }
                usleep(10_000)
                return try session.read(maximum)
            }, stamp: session.stamp, location: session.location, close: session.close)
        }, isCancelled: { false }))
        let c = ScanCoordinator(store: f.store, taskStore: f.tasks, environment: env)
        let other = try await f.tasks.createTask(name: "direct child")
        async let ancestor: Void = c.start(task: f.id, roots: [parent])
        async let direct: Void = c.start(task: other, roots: [children[0]])
        _ = try await (ancestor, direct)
        try require(try f.store.completed(task: f.id) == [parent.appendingPathComponent("0.jpg").path], "nested volumes must not inherit parent authorization")
        try require(try f.store.completed(task: other).count == 1, "separately authorized child volume must scan normally")
        let reopened = try ObservationStore(url: f.store.url)
        let progress = try await ScanCoordinator(store: reopened, taskStore: f.tasks, environment: env).progress(task: f.id)
        try require(Set(progress.excludedRoots) == Set(children.map(\.path)) && !progress.enumerationFinished, "excluded mount ranges must persist as incomplete coverage: actual=\(progress.excludedRoots), expected=\(children.map(\.path)), finished=\(progress.enumerationFinished)")
        try require(p.read { $0.opened == 2 && $0.diskPeak == 1 }, "parent must not open nested volume files while child is scanning")
    }
    static func deviceReplacementAtOpen() async throws {
        let f = try await ResumeFixture(); defer { f.clean() }
        let root = try f.root("a"), p = ScanProbe()
        let envBase = f.environment(p)
        let env = ScanEnvironment(access: envBase.access, identity: envBase.identity, io: FileVerifierIO(stamp: FileVerifierIO.live.stamp, open: { url in
            let session = try FileVerifierIO.live.open(url)
            // Explicit descriptor adapter: the opened file now belongs to another device,
            // while preflight path lookups still report the selected volume.
            return FileReadSession(read: { maximum in
                p.update { $0.reads += 1 }; return try session.read(maximum)
            }, stamp: {
                let stamp = try session.stamp!()
                let device = UInt64(stamp.identity.split(separator: ":")[0])!
                return FileStamp(size: stamp.size, modified: stamp.modified, identity: "\(device + 1):replacement")
            }, location: session.location, close: session.close)
        }, isCancelled: { false }))
        let c = ScanCoordinator(store: f.store, taskStore: f.tasks, environment: env)
        try await c.start(task: f.id, roots: [root])
        let records = try f.store.observations(task: f.id)
        try require(records.count == 1 && records.allSatisfy { !$0.complete && $0.digest == nil }, "descriptor device replacement must remain unknown")
        try require(p.read { $0.reads == 0 }, "reject descriptor device before the first data read")
        let progress = try await c.progress(task: f.id)
        try require(progress.excludedRoots == [root.appendingPathComponent("0.jpg").path] && !progress.enumerationFinished, "read-time device exclusion must persist in coverage")
    }
    static func nestedReplacementAtOpen() async throws {
        let f = try await ResumeFixture(); defer { f.clean() }
        let root = try f.root("a"), p = ScanProbe()
        let original = VolumeIdentity(uuid: "original", fileSystem: "APFS", root: root)
        let replacement = VolumeIdentity(uuid: "replacement", fileSystem: "APFS", root: root.appendingPathComponent("mount"))
        let env = ScanEnvironment(access: { url, _ in ScanLease(root: ScanRoot(url: url, bookmark: Data(), volume: original)) }, identity: { url in
            url == root || !p.read({ $0.changed }) ? original : replacement
        }, io: FileVerifierIO(stamp: FileVerifierIO.live.stamp, open: { url in
            let session = try FileVerifierIO.live.open(url)
            p.update { $0.changed = true }
            return FileReadSession(read: { maximum in
                p.update { $0.reads += 1 }; return try session.read(maximum)
            }, stamp: session.stamp, location: session.location, close: session.close)
        }, isCancelled: { false }))
        let c = ScanCoordinator(store: f.store, taskStore: f.tasks, environment: env)
        try await c.start(task: f.id, roots: [root])
        try require(try f.store.completed(task: f.id).isEmpty && p.read { $0.reads == 0 }, "mount replacement during open must be checked before reading")
        let reopen = try ObservationStore(url: f.store.url)
        let checkpoint = try reopen.checkpoint(task: f.id)
        try require(checkpoint?.excludedRoots == [root.appendingPathComponent("0.jpg").path] && checkpoint?.enumerationFinished == false, "open-time mount exclusion survives reopen")
    }
    static func sameLiveVolume() async throws {
        let f = try await ResumeFixture(); defer { f.clean() }
        let root = try f.root("live")
        let env = ScanEnvironment(access: { url, _ in
            ScanLease(root: ScanRoot(url: url, bookmark: Data(), volume: try ScanEnvironment.liveIdentity(url)))
        }, identity: ScanEnvironment.liveIdentity, io: .live)
        let rootIdentity = try ScanEnvironment.liveIdentity(root)
        let fileIdentity = try ScanEnvironment.liveIdentity(root.appendingPathComponent("0.jpg"))
        try require(rootIdentity == fileIdentity, "live identity lookup must support actual regular files")
        let c = ScanCoordinator(store: f.store, taskStore: f.tasks, environment: env)
        try await c.start(task: f.id, roots: [root])
        try require(try f.store.completed(task: f.id).count == 1, "same-device live descriptor must succeed")
        try require(try await c.progress(task: f.id).enumerationFinished, "same-volume ordinary scope completes")
    }
    static func capacity() async throws {
        let began = Date()
        let f = try await ResumeFixture()
        defer {
            f.clean()
            print("Capacity: files=100001, admitted=100000, elapsed=\(String(format: "%.2f", Date().timeIntervalSince(began)))s, temporaryDirectoryRemoved=\(!FileManager.default.fileExists(atPath: f.base.path))")
        }
        let root = f.base.appendingPathComponent("capacity")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        for i in 0..<100_001 {
            try Data().write(to: root.appendingPathComponent("\(i).jpg"))
        }
        let p = ScanProbe(); p.update { $0.delay = 10_000 }
        // Passing a larger custom limit must never bypass the supported hard limit.
        let c = ScanCoordinator(store: f.store, taskStore: f.tasks, environment: f.environment(p), limits: ScanLimits(files: 100_001))
        let run = Task { try await c.start(task: f.id, roots: [root]) }
        let deadline = Date().addingTimeInterval(120)
        while p.read({ $0.opened == 0 }) {
            if Date() > deadline { await c.cancel(task: f.id); _ = try? await run.value; throw ResumeFailure.assertion("capacity scan did not reach reading") }
            try await Task.sleep(for: .milliseconds(20))
        }
        await c.cancel(task: f.id); try await run.value
        let progress = try await c.progress(task: f.id)
        try require(progress.discovered == 100_000 && progress.fileLimitReached, "hard 100000 file boundary must stop admission")
        try require(progress.completed + progress.failed + progress.pending == 100_000, "coverage must account for every admitted file")
        print("Capacity coverage: completed=\(progress.completed), failed=\(progress.failed), pending=\(progress.pending), fileLimitReached=\(progress.fileLimitReached), peakReaders=\(p.read { $0.peak })")
    }
    static func legacyCheckpoint() async throws {
        let f = try await ResumeFixture(); defer { f.clean() }
        try f.store.record(task: f.id, path: "a.jpg", complete: true, evidence: .hashMatch)
        try f.store.record(task: f.id, path: "b.jpg", complete: false, evidence: .unknown)
        let reopened = try ObservationStore(url: f.store.url)
        try require(try reopened.completed(task: f.id) == ["a.jpg"], "only complete legacy checkpoints reopen")
        try require(try reopened.completed(task: UUID()).isEmpty, "checkpoint isolation")
    }
    static let all: [(String, @Sendable () async throws -> Void)] = [
        ("legacy checkpoint", legacyCheckpoint), ("scan/history/task isolation", scanAndHistory), ("cancel/reopen/resume", cancellationAndResume),
        ("disconnect/authorization/identity/retry", disconnectAndAuthorization), ("quotas", quotas),
        ("global and volume concurrency", concurrency), ("atomic rollback", atomicFailure),
        ("resume interrupted enumeration", resumeInterruptedEnumeration), ("source boundaries", sourceBoundaries),
        ("ambiguous volume identities", identityAmbiguity), ("revisions and digest atomicity", revisionsAndInvalidDigest), ("recheck history and algorithm", recheckHistoryAndAlgorithm), ("nested volume exclusions", nestedVolumeExclusions), ("descriptor device replacement", deviceReplacementAtOpen),
        ("nested mount replacement at open", nestedReplacementAtOpen), ("same live volume", sameLiveVolume), ("100001 real file quota", capacity)
    ]
}
