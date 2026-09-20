import Foundation
import XCTest
@testable import PhotoEvidence

final class FileVerifierTests: XCTestCase {
    func testClassificationRequiresStableStampsAndHash() {
        let stamp = FileStamp(size: 3, modified: Date(timeIntervalSince1970: 1), identity: "x")
        let changed = FileStamp(size: 4, modified: stamp.modified, identity: "x")

        XCTAssertEqual(classify(hashSame: true, bytesCompared: false, before: stamp, after: stamp), .hashMatch)
        XCTAssertEqual(classify(hashSame: true, bytesCompared: true, before: stamp, after: stamp), .byteEqual)
        XCTAssertEqual(classify(hashSame: false, bytesCompared: true, before: stamp, after: stamp), .unknown)
        XCTAssertEqual(classify(hashSame: true, bytesCompared: true, before: stamp, after: changed), .unknown)
    }

    func testRealFilesUseAllChunksAndRequireExactComparisonForByteEvidence() throws {
        let fixture = try VerifierFixture()
        defer { fixture.remove() }
        let a = try fixture.file("one/photo.jpg", [0, 1, 2, 3, 4, 5, 6])
        let equal = try fixture.file("two/renamed.bin", [0, 1, 2, 3, 4, 5, 6])
        let different = try fixture.file("two/photo.jpg", [0, 1, 2, 9, 4, 5, 6])
        let verifier = FileVerifier(authorizedRoots: [fixture.root], chunkSize: 3)

        XCTAssertTrue(try verifier.byteEqualChecked(a: a, b: equal).equal)
        XCTAssertFalse(try verifier.byteEqualChecked(a: a, b: different).equal)
        XCTAssertEqual(try verifier.verify(a: a, b: equal, exactBytes: false).evidence, .hashMatch)
        XCTAssertEqual(try verifier.verify(a: a, b: equal, exactBytes: true).evidence, .byteEqual)
    }

    func testEmptyFilesCompareEqual() throws {
        let fixture = try VerifierFixture()
        defer { fixture.remove() }
        let a = try fixture.file("one/empty", [])
        let b = try fixture.file("two/empty", [])
        XCTAssertTrue(try FileVerifier(authorizedRoots: [fixture.root]).byteEqualChecked(a: a, b: b).equal)
        XCTAssertEqual(
            try digest(url: a).map { String(format: "%02x", $0) }.joined(),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )
    }

    func testEqualBytesAcrossDifferentReadBoundaries() throws {
        let fixture = try VerifierFixture()
        defer { fixture.remove() }
        let a = try fixture.file("a", [0, 1, 2, 3, 4])
        let b = try fixture.file("b", [0, 1, 2, 3, 4])
        let stamp = FileStamp(size: 5, modified: Date(timeIntervalSince1970: 1), identity: "stable")
        var openCount = 0
        let io = FileVerifierIO(stamp: { _ in stamp }, open: { _ in
            openCount += 1
            var chunks = openCount == 1 ? [Data([0, 1]), Data([2, 3, 4]), Data()] : [Data([0, 1, 2]), Data([3, 4]), Data()]
            return FileReadSession(read: { _ in chunks.removeFirst() }, close: {})
        }, isCancelled: { false })

        XCTAssertTrue(try FileVerifier(authorizedRoots: [fixture.root], chunkSize: 3, io: io).byteEqualChecked(a: a, b: b).equal)
    }

    func testOutsideFinalSymlinkIsRejected() throws {
        let fixture = try VerifierFixture()
        defer { fixture.remove() }
        let outside = fixture.base.appendingPathComponent("outside")
        try Data([1]).write(to: outside)
        let link = fixture.root.appendingPathComponent("escape")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: outside)

        XCTAssertThrowsError(try FileVerifier(authorizedRoots: [fixture.root]).digestChecked(url: link)) {
            XCTAssertEqual($0 as? ScopeError, .outsideAuthorizedDirectory)
        }
    }

    func testReadFailureStampChangeShortReadAndCancellationReturnNoResult() throws {
        let fixture = try VerifierFixture()
        defer { fixture.remove() }
        let file = try fixture.file("photo", [0, 1, 2, 3])
        let liveStamp = try FileVerifierIO.live.stamp(file)

        var failedReads: [Result<Data, FileVerificationError>] = [.success(Data([0, 1])), .failure(.unavailable)]
        let failingIO = FileVerifierIO(stamp: { _ in liveStamp }, open: { _ in
            FileReadSession(read: { _ in try failedReads.removeFirst().get() }, close: {})
        }, isCancelled: { false })
        XCTAssertThrowsError(try FileVerifier(authorizedRoots: [fixture.root], chunkSize: 2, io: failingIO).digestChecked(url: file)) {
            XCTAssertEqual($0 as? FileVerificationError, .unavailable)
        }

        var stampCalls = 0
        let changed = FileStamp(size: liveStamp.size, modified: liveStamp.modified.addingTimeInterval(1), identity: liveStamp.identity)
        var changingChunks = [Data([0, 1, 2, 3]), Data()]
        let changingIO = FileVerifierIO(stamp: { _ in liveStamp }, open: { _ in
            FileReadSession(read: { _ in changingChunks.removeFirst() }, stamp: {
                defer { stampCalls += 1 }
                return stampCalls == 0 ? liveStamp : changed
            }, close: {})
        }, isCancelled: { false })
        XCTAssertThrowsError(try FileVerifier(authorizedRoots: [fixture.root], io: changingIO).digestChecked(url: file)) {
            XCTAssertEqual($0 as? FileVerificationError, .changedDuringRead)
        }

        let shortIO = FileVerifierIO(stamp: { _ in liveStamp }, open: { _ in
            var reads = [Data([0]), Data()]
            return FileReadSession(read: { _ in reads.removeFirst() }, close: {})
        }, isCancelled: { false })
        XCTAssertThrowsError(try FileVerifier(authorizedRoots: [fixture.root], io: shortIO).byteEqualChecked(a: file, b: file)) {
            XCTAssertEqual($0 as? FileVerificationError, .shortRead)
        }

        let cancelledIO = FileVerifierIO(stamp: { _ in liveStamp }, open: FileVerifierIO.live.open, isCancelled: { true })
        XCTAssertThrowsError(try FileVerifier(authorizedRoots: [fixture.root], io: cancelledIO).digestChecked(url: file)) {
            XCTAssertEqual($0 as? FileVerificationError, .cancelled)
        }
    }
}

private final class VerifierFixture {
    let base = FileManager.default.temporaryDirectory.appendingPathComponent("FileVerifierTests-\(UUID().uuidString)")
    var root: URL { base.appendingPathComponent("root", isDirectory: true) }
    init() throws { try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true) }
    func file(_ path: String, _ bytes: [UInt8]) throws -> URL {
        let url = root.appendingPathComponent(path)
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data(bytes).write(to: url)
        return url
    }
    func remove() { try? FileManager.default.removeItem(at: base) }
}
