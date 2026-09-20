import Foundation

@MainActor
@main
enum ScopeRegression {
    static func main() async {
        var passed = 0
        var total = 0

        func check(_ name: String, _ body: () async throws -> Bool) async {
            total += 1
            do {
                if try await body() { passed += 1; print("PASS: \(name)") }
                else { print("FAIL: \(name)") }
            } catch { print("FAIL: \(name): \(error)") }
        }

        let root = URL(fileURLWithPath: "/Volumes/Photos")
        await check("collision fails closed") {
            let identity = VolumeIdentity(uuid: "same", fileSystem: "APFS", root: root)
            return !ScopeResolver.sameTrustedVolume(identity, identity, collision: true)
        }
        await check("missing UUID fails closed") {
            let identity = VolumeIdentity(uuid: nil, fileSystem: "APFS", root: root)
            return !ScopeResolver.sameTrustedVolume(identity, identity, collision: false)
        }
        await check("different UUID rejects same volume name") {
            let a = VolumeIdentity(uuid: "a", fileSystem: "APFS", root: root)
            let b = VolumeIdentity(uuid: "b", fileSystem: "APFS", root: root)
            return !ScopeResolver.sameTrustedVolume(a, b, collision: false)
        }
        await check("same-prefix sibling is outside scope") {
            do {
                _ = try ScopeResolver.validateChild(root: root, file: URL(fileURLWithPath: "/Volumes/Photos-old/a.jpg"))
                return false
            } catch ScopeError.outsideAuthorizedDirectory { return true }
        }
        await check("symlink outside scope is rejected") {
            let base = FileManager.default.temporaryDirectory.appendingPathComponent("ScopeRegression-\(UUID().uuidString)")
            defer { try? FileManager.default.removeItem(at: base) }
            let allowed = base.appendingPathComponent("allowed", isDirectory: true)
            let outside = base.appendingPathComponent("outside", isDirectory: true)
            try FileManager.default.createDirectory(at: allowed, withIntermediateDirectories: true)
            try FileManager.default.createDirectory(at: outside, withIntermediateDirectories: true)
            let link = allowed.appendingPathComponent("escape")
            try FileManager.default.createSymbolicLink(at: link, withDestinationURL: outside)
            do { _ = try ScopeResolver.validateChild(root: allowed, file: link); return false }
            catch ScopeError.outsideAuthorizedDirectory { return true }
        }
        await check("cancel returns typed error") {
            let resolver = ScopeResolver(chooseDirectory: { nil })
            do { _ = try await resolver.authorizeDirectory(); return false }
            catch ScopeError.selectionCancelled { return true }
        }
        await check("stale bookmark does not start access") {
            var starts = 0
            let resolver = ScopeResolver(
                resolveBookmark: { _ in (root, true) },
                startAccessing: { _ in starts += 1; return true }
            )
            do { _ = try resolver.restoreAccess(from: Data([1])); return false }
            catch ScopeError.staleBookmark { return starts == 0 }
        }
        await check("access lease stops once") {
            var stops = 0
            let resolver = ScopeResolver(
                resolveBookmark: { _ in (root, false) },
                startAccessing: { _ in true },
                stopAccessing: { _ in stops += 1 }
            )
            var lease: SecurityScopedAccess? = try resolver.restoreAccess(from: Data([1]))
            lease?.close(); lease?.close(); lease = nil
            return stops == 1
        }

        print("ScopeResolver regression: \(passed)/\(total) passed")
        if passed != total { exit(1) }
    }
}
