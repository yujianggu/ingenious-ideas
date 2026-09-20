import Foundation
import XCTest
@testable import PhotoEvidence

@MainActor
final class ScopeTests: XCTestCase {
    func testCollisionAndMissingUUIDFailClosed() {
        let root = URL(fileURLWithPath: "/Volumes/Photos")
        let identified = VolumeIdentity(uuid: "same", fileSystem: "APFS", root: root)
        let missing = VolumeIdentity(uuid: nil, fileSystem: "APFS", root: root)

        XCTAssertFalse(ScopeResolver.sameTrustedVolume(identified, identified, collision: true))
        XCTAssertFalse(ScopeResolver.sameTrustedVolume(missing, missing, collision: false))
    }

    func testSameVolumeNameWithDifferentUUIDIsRejected() {
        let root = URL(fileURLWithPath: "/Volumes/Photos")
        let first = VolumeIdentity(uuid: "disk-a", fileSystem: "APFS", root: root)
        let second = VolumeIdentity(uuid: "disk-b", fileSystem: "APFS", root: root)

        XCTAssertFalse(ScopeResolver.sameTrustedVolume(first, second, collision: false))
    }

    func testCanonicalComponentBoundaryRejectsSamePrefixSibling() {
        XCTAssertThrowsError(try ScopeResolver.validateChild(
            root: URL(fileURLWithPath: "/Volumes/Photos"),
            file: URL(fileURLWithPath: "/Volumes/Photos-old/image.jpg")
        )) { XCTAssertEqual($0 as? ScopeError, .outsideAuthorizedDirectory) }
    }

    func testSymlinkResolvingOutsideRootIsRejected() throws {
        let fixture = try ScopeFixture()
        let link = fixture.root.appendingPathComponent("escape")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: fixture.outside)

        XCTAssertThrowsError(try ScopeResolver.validateChild(root: fixture.root, file: link)) {
            XCTAssertEqual($0 as? ScopeError, .outsideAuthorizedDirectory)
        }
    }

    func testCancellationReturnsTypedError() async {
        let resolver = ScopeResolver(chooseDirectory: { nil })
        do {
            _ = try await resolver.authorizeDirectory()
            XCTFail("Expected cancellation")
        } catch {
            XCTAssertEqual(error as? ScopeError, .selectionCancelled)
        }
    }

    func testStaleBookmarkReturnsTypedErrorWithoutStartingAccess() throws {
        var starts = 0
        let resolver = ScopeResolver(
            resolveBookmark: { _ in (URL(fileURLWithPath: "/Volumes/Photos"), true) },
            startAccessing: { _ in starts += 1; return true }
        )

        XCTAssertThrowsError(try resolver.restoreAccess(from: Data([1]))) {
            XCTAssertEqual($0 as? ScopeError, .staleBookmark)
        }
        XCTAssertEqual(starts, 0)
    }

    func testAccessLeaseStopsExactlyOnce() throws {
        var stops = 0
        let resolver = ScopeResolver(
            resolveBookmark: { _ in (URL(fileURLWithPath: "/Volumes/Photos"), false) },
            startAccessing: { _ in true },
            stopAccessing: { _ in stops += 1 }
        )
        var lease: SecurityScopedAccess? = try resolver.restoreAccess(from: Data([1]))

        lease?.close()
        lease?.close()
        lease = nil

        XCTAssertEqual(stops, 1)
    }
}

private final class ScopeFixture {
    let directory: URL
    let root: URL
    let outside: URL

    init() throws {
        directory = FileManager.default.temporaryDirectory.appendingPathComponent("ScopeTests-\(UUID().uuidString)")
        root = directory.appendingPathComponent("root", isDirectory: true)
        outside = directory.appendingPathComponent("outside", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: outside, withIntermediateDirectories: true)
    }

    deinit { try? FileManager.default.removeItem(at: directory) }
}
