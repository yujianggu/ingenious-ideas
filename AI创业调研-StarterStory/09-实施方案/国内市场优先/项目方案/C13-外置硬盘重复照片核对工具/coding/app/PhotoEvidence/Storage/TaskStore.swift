import Foundation
import SQLite3

enum StoreError: Error, Equatable, CustomStringConvertible {
    case injectedWriteFailure
    case sqlite(code: Int32, message: String)
    case invalidStoredIdentifier(String)
    case taskNotFound(UUID)

    var description: String {
        switch self {
        case .injectedWriteFailure:
            return "injected SQLite write failure"
        case let .sqlite(code, message):
            return "SQLite error \(code): \(message)"
        case let .invalidStoredIdentifier(value):
            return "Invalid task identifier in database: \(value)"
        case let .taskNotFound(id):
            return "Task not found: \(id.uuidString)"
        }
    }
}

enum MutationPhase: Sendable {
    case beforeCommit
}

private func sqliteError(database: OpaquePointer?, code: Int32) -> StoreError {
    let message = database.map { String(cString: sqlite3_errmsg($0)) } ?? "database unavailable"
    return .sqlite(code: code, message: message)
}

private final class SQLiteConnection: @unchecked Sendable {
    let pointer: OpaquePointer

    init(url: URL) throws {
        var opened: OpaquePointer?
        let flags = SQLITE_OPEN_CREATE | SQLITE_OPEN_READWRITE | SQLITE_OPEN_FULLMUTEX
        let code = sqlite3_open_v2(url.path, &opened, flags, nil)
        guard code == SQLITE_OK, let opened else {
            let error = sqliteError(database: opened, code: code)
            if opened != nil { sqlite3_close(opened) }
            throw error
        }
        pointer = opened
    }

    deinit {
        sqlite3_close(pointer)
    }
}

@MainActor
final class TaskStore {
    typealias FailureInjector = @Sendable (MutationPhase) throws -> Void

    private let connection: SQLiteConnection
    private let failureInjector: FailureInjector

    private var database: OpaquePointer { connection.pointer }

    init(url: URL, failureInjector: @escaping FailureInjector = { _ in }) throws {
        self.failureInjector = failureInjector
        connection = try SQLiteConnection(url: url)

        try execute("PRAGMA foreign_keys = ON")
        try execute(
            """
            CREATE TABLE IF NOT EXISTS task (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL
            )
            """
        )
    }

    func createTask(name: String) throws -> UUID {
        let id = UUID()
        try transaction {
            let statement = try prepare("INSERT INTO task(id, name) VALUES(?, ?)")
            defer { sqlite3_finalize(statement) }
            try bind(id.uuidString, at: 1, to: statement)
            try bind(name, at: 2, to: statement)
            try expect(sqlite3_step(statement), equals: SQLITE_DONE)
        }
        return id
    }

    func renameTask(id: UUID, name: String) throws {
        try transaction {
            let statement = try prepare("UPDATE task SET name = ? WHERE id = ?")
            defer { sqlite3_finalize(statement) }
            try bind(name, at: 1, to: statement)
            try bind(id.uuidString, at: 2, to: statement)
            try expect(sqlite3_step(statement), equals: SQLITE_DONE)
            guard sqlite3_changes(database) == 1 else { throw StoreError.taskNotFound(id) }
        }
    }

    func tasks() throws -> [ScanTask] {
        let statement = try prepare("SELECT id, name FROM task ORDER BY sequence")
        defer { sqlite3_finalize(statement) }

        var result: [ScanTask] = []
        while true {
            let code = sqlite3_step(statement)
            if code == SQLITE_DONE { return result }
            try expect(code, equals: SQLITE_ROW)

            let idText = String(cString: sqlite3_column_text(statement, 0))
            guard let id = UUID(uuidString: idText) else {
                throw StoreError.invalidStoredIdentifier(idText)
            }
            let name = String(cString: sqlite3_column_text(statement, 1))
            result.append(ScanTask(id: id, name: name))
        }
    }

    private func transaction(_ mutation: () throws -> Void) throws {
        try execute("BEGIN IMMEDIATE")
        do {
            try mutation()
            try failureInjector(.beforeCommit)
            try execute("COMMIT")
        } catch {
            try? execute("ROLLBACK")
            throw error
        }
    }

    private func execute(_ sql: String) throws {
        let code = sqlite3_exec(database, sql, nil, nil, nil)
        try expect(code, equals: SQLITE_OK)
    }

    private func prepare(_ sql: String) throws -> OpaquePointer? {
        var statement: OpaquePointer?
        let code = sqlite3_prepare_v2(database, sql, -1, &statement, nil)
        guard code == SQLITE_OK else {
            throw sqliteError(database: database, code: code)
        }
        return statement
    }

    private func bind(_ value: String, at index: Int32, to statement: OpaquePointer?) throws {
        let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)
        let code = sqlite3_bind_text(statement, index, value, -1, transient)
        try expect(code, equals: SQLITE_OK)
    }

    private func expect(_ code: Int32, equals expected: Int32) throws {
        guard code == expected else {
            throw sqliteError(database: database, code: code)
        }
    }
}
