import Foundation

@main
enum FileVerifierRegression {
    static func main() throws {
        let fixture = try Fixture()
        defer { fixture.remove() }
        var passed = 0
        var total = 0
        func check(_ name: String, _ body: () throws -> Bool) {
            total += 1
            do { if try body() { passed += 1; print("PASS: \(name)") } else { print("FAIL: \(name)") } }
            catch { print("FAIL: \(name): \(error)") }
        }

        let verifier = FileVerifier(authorizedRoots: [fixture.root], chunkSize: 3)
        let sameA = try fixture.file("a/photo.jpg", [0, 1, 2, 3, 4, 5, 6])
        let sameB = try fixture.file("b/renamed.dat", [0, 1, 2, 3, 4, 5, 6])
        let different = try fixture.file("b/photo.jpg", [0, 1, 2, 9, 4, 5, 6])
        let emptyA = try fixture.file("a/empty", [])
        let emptyB = try fixture.file("b/empty", [])

        check("classification requires stable stamps") {
            let stamp = FileStamp(size: 3, modified: Date(timeIntervalSince1970: 1), identity: "x")
            let changed = FileStamp(size: 4, modified: stamp.modified, identity: "x")
            return classify(hashSame: true, bytesCompared: false, before: stamp, after: stamp) == .hashMatch
                && classify(hashSame: true, bytesCompared: true, before: stamp, after: stamp) == .byteEqual
                && classify(hashSame: true, bytesCompared: true, before: stamp, after: changed) == .unknown
        }
        check("different bytes with equal names and sizes differ") { try !verifier.byteEqualChecked(a: sameA, b: different).equal }
        check("equal bytes across multiple chunks and different names match") { try verifier.byteEqualChecked(a: sameA, b: sameB).equal }
        check("equal bytes match across different read boundaries") {
            let stamp = FileStamp(size: 5, modified: Date(timeIntervalSince1970: 1), identity: "stable")
            var openCount = 0
            let io = FileVerifierIO(stamp: { _ in stamp }, open: { _ in
                openCount += 1
                var chunks = openCount == 1 ? [Data([0, 1]), Data([2, 3, 4]), Data()] : [Data([0, 1, 2]), Data([3, 4]), Data()]
                return FileReadSession(read: { _ in chunks.removeFirst() }, close: {})
            }, isCancelled: { false })
            return try FileVerifier(authorizedRoots: [fixture.root], chunkSize: 3, io: io).byteEqualChecked(a: sameA, b: sameB).equal
        }
        check("empty files match") { try verifier.byteEqualChecked(a: emptyA, b: emptyB).equal }
        check("digest is full SHA-256") {
            try digest(url: emptyA).map { String(format: "%02x", $0) }.joined() == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        }
        check("digest reads every chunk") { try digest(url: sameA) == digest(url: sameB) && digest(url: sameA) != digest(url: different) }
        check("hash match is not promoted without byte comparison") { try verifier.verify(a: sameA, b: sameB, exactBytes: false).evidence == .hashMatch }
        check("exact comparison produces byte equality with stable stamps for both files") {
            let result = try verifier.verify(a: sameA, b: sameB, exactBytes: true)
            return result.evidence == .byteEqual && result.aBefore == result.aAfter && result.bBefore == result.bAfter
        }
        check("outside final symlink is rejected") {
            let outside = try fixture.outsideFile()
            let link = fixture.root.appendingPathComponent("escape")
            try FileManager.default.createSymbolicLink(at: link, withDestinationURL: outside)
            do { _ = try verifier.digestChecked(url: link); return false }
            catch ScopeError.outsideAuthorizedDirectory { return true }
        }
        check("read failure returns no digest") {
            var reads: [Result<Data, FileVerificationError>] = [.success(Data([0, 1, 2])), .failure(.unavailable)]
            let io = FileVerifierIO(stamp: FileVerifierIO.live.stamp, open: { _ in
                FileReadSession(read: { _ in try reads.removeFirst().get() }, close: {})
            }, isCancelled: { false })
            do { _ = try FileVerifier(authorizedRoots: [fixture.root], chunkSize: 3, io: io).digestChecked(url: sameA); return false }
            catch FileVerificationError.unavailable { return true }
        }
        check("stamp change returns no digest") {
            let before = try FileVerifierIO.live.stamp(sameA)
            let changed = FileStamp(size: before.size, modified: before.modified.addingTimeInterval(1), identity: before.identity)
            var calls = 0
            var chunks = [Data([0, 1, 2]), Data([3, 4, 5]), Data([6]), Data()]
            let io = FileVerifierIO(stamp: { _ in before }, open: { _ in
                FileReadSession(
                    read: { _ in chunks.removeFirst() },
                    stamp: { defer { calls += 1 }; return calls == 0 ? before : changed },
                    close: {}
                )
            }, isCancelled: { false })
            do { _ = try FileVerifier(authorizedRoots: [fixture.root], chunkSize: 3, io: io).digestChecked(url: sameA); return false }
            catch FileVerificationError.changedDuringRead { return true }
        }
        check("short read returns no equality") {
            let before = try FileVerifierIO.live.stamp(sameA)
            let io = FileVerifierIO(stamp: { _ in before }, open: { _ in
                var reads = [Data([0, 1]), Data()]
                return FileReadSession(read: { _ in reads.removeFirst() }, close: {})
            }, isCancelled: { false })
            do { _ = try FileVerifier(authorizedRoots: [fixture.root], io: io).byteEqualChecked(a: sameA, b: sameA); return false }
            catch FileVerificationError.shortRead { return true }
        }
        check("cancellation returns no digest") {
            let io = FileVerifierIO(stamp: FileVerifierIO.live.stamp, open: FileVerifierIO.live.open, isCancelled: { true })
            do { _ = try FileVerifier(authorizedRoots: [fixture.root], io: io).digestChecked(url: sameA); return false }
            catch FileVerificationError.cancelled { return true }
        }
        check("different lengths terminate in both operand orders") {
            let short = try fixture.file("short", [1])
            let long = try fixture.file("long", [1, 2, 3, 4])
            var checks = 0
            let boundedIO = FileVerifierIO(stamp: FileVerifierIO.live.stamp, open: FileVerifierIO.live.open, isCancelled: { checks += 1; return checks > 10_000 })
            let bounded = FileVerifier(authorizedRoots: [fixture.root], chunkSize: 2, io: boundedIO)
            return try !bounded.byteEqualChecked(a: short, b: long).equal && !bounded.byteEqualChecked(a: long, b: short).equal
        }
        check("empty and nonempty terminate in both operand orders") {
            let nonempty = try fixture.file("nonempty", [1, 2])
            var checks = 0
            let boundedIO = FileVerifierIO(stamp: FileVerifierIO.live.stamp, open: FileVerifierIO.live.open, isCancelled: { checks += 1; return checks > 10_000 })
            let bounded = FileVerifier(authorizedRoots: [fixture.root], chunkSize: 2, io: boundedIO)
            return try !bounded.byteEqualChecked(a: emptyA, b: nonempty).equal && !bounded.byteEqualChecked(a: nonempty, b: emptyA).equal
        }
        check("one-sided premature EOF throws short read") {
            let stamp = FileStamp(size: 3, modified: Date(timeIntervalSince1970: 1), identity: "stable")
            var opens = 0
            var checks = 0
            let io = FileVerifierIO(stamp: { _ in stamp }, open: { _ in
                opens += 1
                var chunks = opens == 1 ? [Data([1]), Data()] : [Data([1, 2, 3]), Data()]
                return FileReadSession(read: { _ in chunks.removeFirst() }, close: {})
            }, isCancelled: { checks += 1; return checks > 10_000 })
            do { _ = try FileVerifier(authorizedRoots: [fixture.root], io: io).byteEqualChecked(a: sameA, b: sameB); return false }
            catch FileVerificationError.shortRead { return true }
        }
        check("replacement with outside final symlink is rejected before read") {
            let victim = try fixture.file("victim", [7])
            let outside = try fixture.outsideFile(bytes: [8, 9, 10])
            let io = FileVerifierIO(stamp: FileVerifierIO.live.stamp, open: { url in
                try FileManager.default.removeItem(at: victim)
                try FileManager.default.createSymbolicLink(at: victim, withDestinationURL: outside)
                return try FileVerifierIO.live.open(url)
            }, isCancelled: { false })
            do { _ = try FileVerifier(authorizedRoots: [fixture.root], io: io).digestChecked(url: victim); return false }
            catch { return true }
        }
        check("cancellation during digest EOF read returns no digest") {
            let stamp = FileStamp(size: 1, modified: Date(timeIntervalSince1970: 1), identity: "stable")
            var cancelled = false
            var reads = 0
            let io = FileVerifierIO(stamp: { _ in stamp }, open: { _ in
                FileReadSession(read: { _ in
                    defer { reads += 1 }
                    if reads == 0 { return Data([1]) }
                    cancelled = true
                    return Data()
                }, close: {})
            }, isCancelled: { cancelled })
            do { _ = try FileVerifier(authorizedRoots: [fixture.root], io: io).digestChecked(url: sameA); return false }
            catch FileVerificationError.cancelled { return true }
        }
        check("cancellation during comparison EOF read returns no equality") {
            let stamp = FileStamp(size: 1, modified: Date(timeIntervalSince1970: 1), identity: "stable")
            var cancelled = false
            var opens = 0
            let io = FileVerifierIO(stamp: { _ in stamp }, open: { _ in
                opens += 1
                let thisOpen = opens
                var reads = 0
                return FileReadSession(read: { _ in
                    defer { reads += 1 }
                    if reads == 0 { return Data([1]) }
                    if thisOpen == 2 { cancelled = true }
                    return Data()
                }, close: {})
            }, isCancelled: { cancelled })
            do { _ = try FileVerifier(authorizedRoots: [fixture.root], io: io).byteEqualChecked(a: sameA, b: sameB); return false }
            catch FileVerificationError.cancelled { return true }
        }
        check("cancellation during digest final stamp returns no digest") {
            try digestFinalStampCancellationIsRejected(root: fixture.root, file: sameA)
        }
        check("cancellation during comparison final stamp returns no equality") {
            try comparisonFinalStampCancellationIsRejected(root: fixture.root, a: sameA, b: sameB)
        }

        print("FileVerifier regression: \(passed)/\(total) passed")
        if passed != total { exit(1) }
    }

    private static func digestFinalStampCancellationIsRejected(root: URL, file: URL) throws -> Bool {
        let stamp = FileStamp(size: 1, modified: Date(timeIntervalSince1970: 1), identity: "stable")
        var cancelled = false
        var stampCalls = 0
        var chunks: [Data] = [Data([1]), Data()]
        let io = FileVerifierIO(stamp: { _ in stamp }, open: { _ in
            FileReadSession(read: { _ in chunks.removeFirst() }, stamp: {
                defer { stampCalls += 1 }
                if stampCalls == 1 { cancelled = true }
                return stamp
            }, close: {})
        }, isCancelled: { cancelled })
        do { _ = try FileVerifier(authorizedRoots: [root], io: io).digestChecked(url: file); return false }
        catch FileVerificationError.cancelled { return true }
    }

    private static func comparisonFinalStampCancellationIsRejected(root: URL, a: URL, b: URL) throws -> Bool {
        let stamp = FileStamp(size: 1, modified: Date(timeIntervalSince1970: 1), identity: "stable")
        var cancelled = false
        var opens = 0
        let io = FileVerifierIO(stamp: { _ in stamp }, open: { _ in
            opens += 1
            let thisOpen = opens
            var chunks: [Data] = [Data([1]), Data()]
            var stampCalls = 0
            return FileReadSession(read: { _ in chunks.removeFirst() }, stamp: {
                defer { stampCalls += 1 }
                if thisOpen == 1 && stampCalls == 1 { cancelled = true }
                return stamp
            }, close: {})
        }, isCancelled: { cancelled })
        do { _ = try FileVerifier(authorizedRoots: [root], io: io).byteEqualChecked(a: a, b: b); return false }
        catch FileVerificationError.cancelled { return true }
    }
}

private final class Fixture {
    let base = FileManager.default.temporaryDirectory.appendingPathComponent("FileVerifierRegression-\(UUID().uuidString)")
    var root: URL { base.appendingPathComponent("root", isDirectory: true) }
    init() throws { try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true) }
    func file(_ path: String, _ bytes: [UInt8]) throws -> URL {
        let url = root.appendingPathComponent(path)
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data(bytes).write(to: url)
        return url
    }
    func outsideFile(bytes: [UInt8] = [1]) throws -> URL { let url = base.appendingPathComponent("outside-\(UUID().uuidString)"); try Data(bytes).write(to: url); return url }
    func remove() { try? FileManager.default.removeItem(at: base) }
}
