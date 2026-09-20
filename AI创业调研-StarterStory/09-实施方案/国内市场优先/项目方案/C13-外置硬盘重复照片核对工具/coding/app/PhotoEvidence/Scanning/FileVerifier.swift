import CryptoKit
import Darwin
import Foundation

enum Evidence: String, Codable, Sendable {
    case unknown
    case hashMatch
    case byteEqual
}

struct FileStamp: Equatable, Codable, Sendable {
    let size: UInt64
    let modified: Date
    let identity: String
}

func classify(hashSame: Bool, bytesCompared: Bool, before: FileStamp, after: FileStamp) -> Evidence {
    guard before == after, hashSame else { return .unknown }
    return bytesCompared ? .byteEqual : .hashMatch
}

enum FileVerificationError: Error, Equatable { case unavailable, changedDuringRead, shortRead, cancelled }
struct CheckedDigest: Equatable, Sendable { let value: Data; let before: FileStamp; let after: FileStamp }
struct CheckedByteComparison: Equatable {
    let equal: Bool
    let aBefore: FileStamp; let aAfter: FileStamp
    let bBefore: FileStamp; let bAfter: FileStamp
}
struct FileVerification: Equatable {
    let evidence: Evidence
    let aBefore: FileStamp; let aAfter: FileStamp
    let bBefore: FileStamp; let bAfter: FileStamp
}

struct FileReadSession: @unchecked Sendable {
    let read: (_ maximumBytes: Int) throws -> Data
    let stamp: (() throws -> FileStamp)?
    let location: URL?
    let close: () -> Void

    init(
        read: @escaping (_ maximumBytes: Int) throws -> Data,
        stamp: (() throws -> FileStamp)? = nil,
        location: URL? = nil,
        close: @escaping () -> Void
    ) {
        self.read = read
        self.stamp = stamp
        self.location = location
        self.close = close
    }
}

struct FileVerifierIO: @unchecked Sendable {
    let stamp: (URL) throws -> FileStamp
    let open: (URL) throws -> FileReadSession
    let isCancelled: () -> Bool
    static let live = FileVerifierIO(stamp: FileVerifier.liveStamp, open: FileVerifier.liveOpen, isCancelled: { false })
}

struct FileVerifier {
    static let defaultChunkSize = 64 * 1024
    private let authorizedRoots: [URL]
    private let chunkSize: Int
    private let io: FileVerifierIO

    init(authorizedRoots: [URL], chunkSize: Int = defaultChunkSize, io: FileVerifierIO = .live) {
        self.authorizedRoots = authorizedRoots
        self.chunkSize = max(1, chunkSize)
        self.io = io
    }

    func digestChecked(url: URL) throws -> CheckedDigest {
        let canonical = try authorizedURL(for: url)
        let session = try io.open(canonical)
        defer { session.close() }
        try validateOpenedLocation(session.location)
        let before = try normalizedStamp(canonical, session: session)
        try checkCancellation()
        var hasher = SHA256()
        var count: UInt64 = 0
        while true {
            try checkCancellation()
            let data = try session.read(chunkSize)
            try checkCancellation()
            if data.isEmpty { break }
            count = try adding(data.count, to: count, expected: before.size)
            hasher.update(data: data)
        }
        guard count == before.size else { throw FileVerificationError.shortRead }
        let after = try normalizedStamp(canonical, session: session)
        try checkCancellation()
        guard before == after else { throw FileVerificationError.changedDuringRead }
        return CheckedDigest(value: Data(hasher.finalize()), before: before, after: after)
    }

    func byteEqualChecked(a: URL, b: URL) throws -> CheckedByteComparison {
        let canonicalA = try authorizedURL(for: a)
        let canonicalB = try authorizedURL(for: b)
        let aSession = try io.open(canonicalA)
        defer { aSession.close() }
        try validateOpenedLocation(aSession.location)
        let bSession = try io.open(canonicalB)
        defer { bSession.close() }
        try validateOpenedLocation(bSession.location)
        let aBefore = try normalizedStamp(canonicalA, session: aSession)
        try checkCancellation()
        let bBefore = try normalizedStamp(canonicalB, session: bSession)
        try checkCancellation()
        var aCount: UInt64 = 0
        var bCount: UInt64 = 0
        var equal = aBefore.size == bBefore.size
        var aBuffer = Data()
        var bBuffer = Data()
        var aFinished = false
        var bFinished = false
        while true {
            try checkCancellation()
            if aBuffer.isEmpty && !aFinished {
                let data = try aSession.read(chunkSize)
                try checkCancellation()
                aFinished = data.isEmpty
                aCount = try adding(data.count, to: aCount, expected: aBefore.size)
                aBuffer.append(data)
            }
            if bBuffer.isEmpty && !bFinished {
                let data = try bSession.read(chunkSize)
                try checkCancellation()
                bFinished = data.isEmpty
                bCount = try adding(data.count, to: bCount, expected: bBefore.size)
                bBuffer.append(data)
            }
            let comparedCount = min(aBuffer.count, bBuffer.count)
            if comparedCount > 0 {
                if aBuffer.prefix(comparedCount) != bBuffer.prefix(comparedCount) { equal = false }
                aBuffer.removeFirst(comparedCount)
                bBuffer.removeFirst(comparedCount)
            }
            if aFinished && aBuffer.isEmpty && !bBuffer.isEmpty {
                equal = false
                bBuffer.removeAll(keepingCapacity: true)
            }
            if bFinished && bBuffer.isEmpty && !aBuffer.isEmpty {
                equal = false
                aBuffer.removeAll(keepingCapacity: true)
            }
            if aFinished && bFinished {
                if !aBuffer.isEmpty || !bBuffer.isEmpty { equal = false }
                break
            }
        }
        guard aCount == aBefore.size, bCount == bBefore.size else { throw FileVerificationError.shortRead }
        let aAfter = try normalizedStamp(canonicalA, session: aSession)
        try checkCancellation()
        let bAfter = try normalizedStamp(canonicalB, session: bSession)
        try checkCancellation()
        guard aBefore == aAfter, bBefore == bAfter else { throw FileVerificationError.changedDuringRead }
        return CheckedByteComparison(equal: equal, aBefore: aBefore, aAfter: aAfter, bBefore: bBefore, bAfter: bAfter)
    }

    func verify(a: URL, b: URL, exactBytes: Bool) throws -> FileVerification {
        let aDigest = try digestChecked(url: a)
        let bDigest = try digestChecked(url: b)
        guard aDigest.value == bDigest.value else {
            return FileVerification(evidence: .unknown, aBefore: aDigest.before, aAfter: aDigest.after, bBefore: bDigest.before, bAfter: bDigest.after)
        }
        guard exactBytes else {
            return FileVerification(evidence: .hashMatch, aBefore: aDigest.before, aAfter: aDigest.after, bBefore: bDigest.before, bAfter: bDigest.after)
        }
        let comparison = try byteEqualChecked(a: a, b: b)
        return FileVerification(evidence: comparison.equal ? .byteEqual : .unknown, aBefore: comparison.aBefore, aAfter: comparison.aAfter, bBefore: comparison.bBefore, bAfter: comparison.bAfter)
    }

    private func authorizedURL(for url: URL) throws -> URL {
        for root in authorizedRoots {
            if let canonical = try? ScopeResolver.validateChild(root: root, file: url) { return canonical }
        }
        throw ScopeError.outsideAuthorizedDirectory
    }

    private func normalizedStamp(_ url: URL, session: FileReadSession) throws -> FileStamp {
        do { return try session.stamp?() ?? io.stamp(url) }
        catch let error as FileVerificationError { throw error }
        catch { throw FileVerificationError.unavailable }
    }
    private func validateOpenedLocation(_ location: URL?) throws {
        guard let location else { return }
        for root in authorizedRoots {
            if (try? ScopeResolver.validateChild(root: root, file: location)) != nil { return }
        }
        throw ScopeError.outsideAuthorizedDirectory
    }
    private func checkCancellation() throws { if io.isCancelled() { throw FileVerificationError.cancelled } }
    private func adding(_ bytes: Int, to count: UInt64, expected: UInt64) throws -> UInt64 {
        let (next, overflow) = count.addingReportingOverflow(UInt64(bytes))
        guard !overflow, next <= expected else { throw FileVerificationError.shortRead }
        return next
    }

    fileprivate static func liveOpen(_ url: URL) throws -> FileReadSession {
        let descriptor = url.path.withCString { Darwin.open($0, O_RDONLY | O_CLOEXEC | O_NOFOLLOW) }
        guard descriptor >= 0 else { throw FileVerificationError.unavailable }
        var path = [CChar](repeating: 0, count: Int(MAXPATHLEN))
        guard fcntl(descriptor, F_GETPATH, &path) == 0 else {
            Darwin.close(descriptor)
            throw FileVerificationError.unavailable
        }
        let pathBytes = path.prefix { $0 != 0 }.map { UInt8(bitPattern: $0) }
        let location = URL(fileURLWithPath: String(decoding: pathBytes, as: UTF8.self))
        let handle = FileHandle(fileDescriptor: descriptor, closeOnDealloc: false)
        return FileReadSession(read: { maximum in
            do { return try handle.read(upToCount: maximum) ?? Data() }
            catch { throw FileVerificationError.unavailable }
        }, stamp: { try descriptorStamp(descriptor) }, location: location, close: { try? handle.close() })
    }

    fileprivate static func liveStamp(_ url: URL) throws -> FileStamp {
        var info = stat()
        let status = url.path.withCString { fstatat(AT_FDCWD, $0, &info, 0) }
        guard status == 0 else { throw FileVerificationError.unavailable }
        return stamp(from: info)
    }

    private static func descriptorStamp(_ descriptor: Int32) throws -> FileStamp {
        var info = stat()
        guard fstat(descriptor, &info) == 0 else { throw FileVerificationError.unavailable }
        return stamp(from: info)
    }

    private static func stamp(from info: stat) -> FileStamp {
        let modified = Date(timeIntervalSince1970: TimeInterval(info.st_mtimespec.tv_sec) + TimeInterval(info.st_mtimespec.tv_nsec) / 1_000_000_000)
        return FileStamp(size: UInt64(info.st_size), modified: modified, identity: "\(UInt64(info.st_dev)):\(UInt64(info.st_ino))")
    }
}

func digest(url: URL) throws -> Data {
    try FileVerifier(authorizedRoots: [url.deletingLastPathComponent()]).digestChecked(url: url).value
}

func byteEqual(a: URL, b: URL) throws -> Bool {
    try FileVerifier(authorizedRoots: [a.deletingLastPathComponent(), b.deletingLastPathComponent()]).byteEqualChecked(a: a, b: b).equal
}
