import Foundation
import XCTest
@testable import PhotoEvidence

@MainActor
final class TaskStoreTests: XCTestCase {
    func testCreatedTaskSurvivesRealDatabaseReopen() throws {
        let fixture = try DatabaseFixture()
        let id = try TaskStore(url: fixture.databaseURL).createTask(name: "家庭照片")

        let reopened = try TaskStore(url: fixture.databaseURL)

        XCTAssertEqual(try reopened.tasks(), [ScanTask(id: id, name: "家庭照片")])
    }

    func testSameNameCreatesSeparateTaskIdentifiers() throws {
        let fixture = try DatabaseFixture()
        let store = try TaskStore(url: fixture.databaseURL)

        let first = try store.createTask(name: "家庭照片")
        let second = try store.createTask(name: "家庭照片")

        XCTAssertNotEqual(first, second)
        XCTAssertEqual(try store.tasks().map(\.id), [first, second])
    }

    func testRenamedTaskSurvivesRealDatabaseReopen() throws {
        let fixture = try DatabaseFixture()
        let store = try TaskStore(url: fixture.databaseURL)
        let id = try store.createTask(name: "旧名称")

        try store.renameTask(id: id, name: "新名称")
        let reopened = try TaskStore(url: fixture.databaseURL)

        XCTAssertEqual(try reopened.tasks(), [ScanTask(id: id, name: "新名称")])
    }

    func testCommitFailureThrowsAndDoesNotPersistFalseSuccess() throws {
        let fixture = try DatabaseFixture()
        let failing = try TaskStore(url: fixture.databaseURL) { phase in
            if case .beforeCommit = phase { throw StoreError.injectedWriteFailure }
        }

        XCTAssertThrowsError(try failing.createTask(name: "不应成功")) { error in
            XCTAssertEqual(error as? StoreError, .injectedWriteFailure)
        }
        let reopened = try TaskStore(url: fixture.databaseURL)
        XCTAssertEqual(try reopened.tasks(), [])
    }
}

private final class DatabaseFixture {
    let directoryURL: URL
    let databaseURL: URL

    init() throws {
        directoryURL = FileManager.default.temporaryDirectory
            .appendingPathComponent("PhotoEvidenceTests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directoryURL, withIntermediateDirectories: true)
        databaseURL = directoryURL.appendingPathComponent("tasks.sqlite")
    }

    deinit { try? FileManager.default.removeItem(at: directoryURL) }
}
