import Foundation
import SQLite3

struct ScanRoot: Codable, Equatable, Sendable {
    let url: URL
    let bookmark: Data
    let volume: VolumeIdentity
}
struct ScanObservation: Codable, Equatable, Sendable {
    let taskID: UUID
    let path: String
    var revision: Int
    var root: ScanRoot?
    var complete = false
    var evidence: Evidence = .unknown
    var digest: Data?
    var before: FileStamp?
    var after: FileStamp?
    var checkedAt: Date?
    var failure: String?
    var algorithm = "sha256-full-v1"
    // Online status is deliberately never persisted. A reopened index is historical.
    var currentlyOnline: Bool { false }
    var isEmptyFile: Bool { before?.size == 0 }
}
struct ScanCheckpoint: Codable, Sendable {
    let taskID: UUID
    var revision: Int
    var roots: [ScanRoot]
    var excludedRoots: [String] = []
    var fileLimitReached = false
    var enumerationFailures: [String] = []
    var enumerationFinished = false
    var cancelled = false
}
enum ObservationError: Error { case invalidDigest, corruptRecord }

private final class ObservationConnection: @unchecked Sendable {
    let pointer: OpaquePointer
    init(url: URL) throws {
        var opened: OpaquePointer?
        let code = sqlite3_open_v2(url.path, &opened, SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX, nil)
        guard code == SQLITE_OK, let opened else {
            if let opened { sqlite3_close(opened) }
            throw StoreError.sqlite(code: code, message: "Cannot open observation database")
        }
        pointer = opened
    }
    deinit { sqlite3_close(pointer) }
}

/// All SQLite use, including transactions, is serialized by the lock. No pointer escapes.
final class ObservationStore: @unchecked Sendable {
    let url: URL
    private let connection: ObservationConnection
    private var db: OpaquePointer { connection.pointer }
    private let lock = NSLock()
    private let beforeCommit: @Sendable () throws -> Void

    init(url: URL, beforeCommit: @escaping @Sendable () throws -> Void = {}) throws {
        self.url = url
        self.beforeCommit = beforeCommit
        connection = try ObservationConnection(url: url)
        try execute("PRAGMA busy_timeout=5000")
        try execute("CREATE TABLE IF NOT EXISTS observation(task_id TEXT NOT NULL,path TEXT NOT NULL,scan_revision INTEGER NOT NULL,complete INTEGER NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(task_id,path,scan_revision))")
        try execute("CREATE TABLE IF NOT EXISTS scan_checkpoint(task_id TEXT NOT NULL,scan_revision INTEGER NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(task_id,scan_revision))")
    }

    // Legacy checkpoint API: records completion only; no fabricated digest or group membership.
    func record(task: UUID, path: String, complete: Bool, evidence: Evidence) throws {
        var value = ScanObservation(taskID: task, path: path, revision: 0)
        value.complete = complete
        value.evidence = complete ? evidence : .unknown
        try save([value])
    }
    func completed(task: UUID) throws -> [String] {
        try observations(task: task).filter(\.complete).map(\.path).sorted()
    }
    func observations(task: UUID, revision: Int? = nil) throws -> [ScanObservation] {
        try lock.withLock {
            let rev = try revision ?? latestRevisionUnlocked(task: task)
            return try query("SELECT payload FROM observation WHERE task_id=? AND scan_revision=? ORDER BY path", [task.uuidString, String(rev)], as: ScanObservation.self)
        }
    }
    func checkpoint(task: UUID) throws -> ScanCheckpoint? {
        try lock.withLock {
            try query("SELECT payload FROM scan_checkpoint WHERE task_id=? ORDER BY scan_revision DESC LIMIT 1", [task.uuidString], as: ScanCheckpoint.self).first
        }
    }
    func createCheckpoint(task: UUID, roots: [ScanRoot], excluded: [String]) throws -> ScanCheckpoint {
        try lock.withLock {
            var result: ScanCheckpoint!
            try transaction {
                result = ScanCheckpoint(taskID: task, revision: try latestRevisionUnlocked(task: task) + 1, roots: roots, excludedRoots: excluded)
                try writeCheckpoint(result)
            }
            return result
        }
    }
    func save(checkpoint: ScanCheckpoint) throws {
        try lock.withLock { try transaction { try writeCheckpoint(checkpoint) } }
    }
    func save(_ observations: [ScanObservation]) throws {
        try lock.withLock { try transaction { for observation in observations { try writeObservation(observation) } } }
    }
    /// An explicit recheck of completed evidence forks the whole visible snapshot atomically.
    /// Failure during a new attempt cannot erase the previous evidence revision.
    func fork(checkpoint: ScanCheckpoint, observations: [ScanObservation]) throws -> ScanCheckpoint {
        try lock.withLock {
            var next = checkpoint
            try transaction {
                next.revision = try latestRevisionUnlocked(task: checkpoint.taskID) + 1
                try writeCheckpoint(next)
                for var observation in observations {
                    observation.revision = next.revision
                    try writeObservation(observation)
                }
            }
            return next
        }
    }
    private func writeObservation(_ observation: ScanObservation) throws {
        if let digest = observation.digest {
            guard observation.complete, digest.count == 32,
                  let before = observation.before, before == observation.after,
                  observation.checkedAt != nil else { throw ObservationError.invalidDigest }
        }
        guard observation.complete || (observation.digest == nil && observation.evidence == .unknown) else { throw ObservationError.invalidDigest }
        try run("INSERT OR REPLACE INTO observation(task_id,path,scan_revision,complete,payload) VALUES(?,?,?,?,?)", [observation.taskID.uuidString, observation.path, String(observation.revision), observation.complete ? "1" : "0", try encode(observation)])
    }
    private func latestRevisionUnlocked(task: UUID) throws -> Int {
        let values: [ScanCheckpoint] = try query("SELECT payload FROM scan_checkpoint WHERE task_id=? ORDER BY scan_revision DESC LIMIT 1", [task.uuidString], as: ScanCheckpoint.self)
        return values.first?.revision ?? 0
    }
    private func writeCheckpoint(_ checkpoint: ScanCheckpoint) throws {
        try run("INSERT OR REPLACE INTO scan_checkpoint(task_id,scan_revision,payload) VALUES(?,?,?)", [checkpoint.taskID.uuidString, String(checkpoint.revision), try encode(checkpoint)])
    }
    private func encode<T: Encodable>(_ value: T) throws -> String {
        String(decoding: try JSONEncoder().encode(value), as: UTF8.self)
    }
    private func transaction(_ action: () throws -> Void) throws {
        try execute("BEGIN IMMEDIATE")
        do { try action(); try beforeCommit(); try execute("COMMIT") }
        catch { try? execute("ROLLBACK"); throw error }
    }
    private func execute(_ sql: String) throws { try check(sqlite3_exec(db, sql, nil, nil, nil), SQLITE_OK) }
    private func statement(_ sql: String, _ args: [String]) throws -> OpaquePointer {
        var result: OpaquePointer?
        try check(sqlite3_prepare_v2(db, sql, -1, &result, nil), SQLITE_OK)
        guard let result else { throw ObservationError.corruptRecord }
        do {
            let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)
            for (i, arg) in args.enumerated() { try check(sqlite3_bind_text(result, Int32(i + 1), arg, -1, transient), SQLITE_OK) }
        } catch { sqlite3_finalize(result); throw error }
        return result
    }
    private func run(_ sql: String, _ args: [String]) throws {
        let stmt = try statement(sql, args); defer { sqlite3_finalize(stmt) }
        try check(sqlite3_step(stmt), SQLITE_DONE)
    }
    private func query<T: Decodable>(_ sql: String, _ args: [String], as: T.Type) throws -> [T] {
        let stmt = try statement(sql, args); defer { sqlite3_finalize(stmt) }
        var result: [T] = []
        while true {
            let code = sqlite3_step(stmt)
            if code == SQLITE_DONE { return result }
            try check(code, SQLITE_ROW)
            guard let bytes = sqlite3_column_text(stmt, 0) else { throw ObservationError.corruptRecord }
            result.append(try JSONDecoder().decode(T.self, from: Data(String(cString: bytes).utf8)))
        }
    }
    private func check(_ code: Int32, _ expected: Int32) throws {
        guard code == expected else { throw StoreError.sqlite(code: code, message: String(cString: sqlite3_errmsg(db))) }
    }
}
