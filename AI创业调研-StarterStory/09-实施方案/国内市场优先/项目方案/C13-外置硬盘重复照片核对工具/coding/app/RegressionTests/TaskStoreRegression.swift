import Foundation

@main
@MainActor
struct TaskStoreRegression {
    static func main() {
        var failures = 0
        run("created task survives real database reopen", failures: &failures, testCreatedTaskSurvivesReopen)
        run("same names receive separate identifiers", failures: &failures, testSameNamesReceiveSeparateIdentifiers)
        run("rename survives real database reopen", failures: &failures, testRenameSurvivesReopen)
        run("commit failure throws without false success", failures: &failures, testCommitFailureRollsBack)
        print("TaskStore regression: \(4 - failures)/4 passed")
        if failures != 0 { exit(1) }
    }

    private static func run(_ name: String, failures: inout Int, _ body: () throws -> Void) {
        do { try body(); print("PASS: \(name)") }
        catch { failures += 1; print("FAIL: \(name): \(error)") }
    }

    private static func withDatabase<T>(_ body: (URL) throws -> T) throws -> T {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("PhotoEvidenceRegression-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        return try body(directory.appendingPathComponent("tasks.sqlite"))
    }

    private static func require(_ condition: @autoclosure () -> Bool, _ message: String) throws {
        if !condition() { throw RegressionFailure.assertion(message) }
    }

    private static func testCreatedTaskSurvivesReopen() throws {
        try withDatabase { url in
            let id = try TaskStore(url: url).createTask(name: "家庭照片")
            let rows = try TaskStore(url: url).tasks()
            try require(rows == [ScanTask(id: id, name: "家庭照片")], "reopened rows differ")
        }
    }

    private static func testSameNamesReceiveSeparateIdentifiers() throws {
        try withDatabase { url in
            let store = try TaskStore(url: url)
            let first = try store.createTask(name: "家庭照片")
            let second = try store.createTask(name: "家庭照片")
            try require(first != second, "identifiers are equal")
            let identifiers = try store.tasks().map(\.id)
            try require(identifiers == [first, second], "both rows were not retained")
        }
    }

    private static func testRenameSurvivesReopen() throws {
        try withDatabase { url in
            let store = try TaskStore(url: url)
            let id = try store.createTask(name: "旧名称")
            try store.renameTask(id: id, name: "新名称")
            let rows = try TaskStore(url: url).tasks()
            try require(rows == [ScanTask(id: id, name: "新名称")], "rename was not persisted")
        }
    }

    private static func testCommitFailureRollsBack() throws {
        try withDatabase { url in
            let store = try TaskStore(url: url) { phase in
                if case .beforeCommit = phase { throw StoreError.injectedWriteFailure }
            }
            do {
                _ = try store.createTask(name: "不应成功")
                throw RegressionFailure.assertion("create returned a false-success identifier")
            } catch StoreError.injectedWriteFailure {}
            let rows = try TaskStore(url: url).tasks()
            try require(rows.isEmpty, "failed transaction persisted a row")
        }
    }
}

private enum RegressionFailure: Error {
    case assertion(String)
}
