import AppKit
import Foundation

enum ScopeError: Error, Equatable, LocalizedError {
    case selectionCancelled
    case outsideAuthorizedDirectory
    case staleBookmark
    case accessDenied
    case invalidBookmark

    var errorDescription: String? {
        switch self {
        case .selectionCancelled: "未选择文件夹。"
        case .outsideAuthorizedDirectory: "所选文件不在已授权文件夹内。"
        case .staleBookmark: "文件夹授权已过期，请重新选择。"
        case .accessDenied: "无法访问已授权文件夹，请重新授权。"
        case .invalidBookmark: "保存的文件夹授权无效，请重新选择。"
        }
    }
}

struct AuthorizedDirectory: Equatable, Sendable {
    let url: URL
    let bookmark: Data
}

final class SecurityScopedAccess {
    let url: URL
    private let stopper: AccessStopper

    init(url: URL, stopAccessing: @escaping () -> Void) {
        self.url = url
        stopper = AccessStopper(stopAccessing)
    }

    func close() { stopper.stop() }
}

private final class AccessStopper: @unchecked Sendable {
    private let lock = NSLock()
    private var stopAccessing: (() -> Void)?

    init(_ stopAccessing: @escaping () -> Void) { self.stopAccessing = stopAccessing }

    func stop() {
        lock.lock()
        let action = stopAccessing
        stopAccessing = nil
        lock.unlock()
        action?()
    }

    deinit { stop() }
}

@MainActor
final class ScopeResolver {
    typealias DirectoryChooser = @MainActor () async -> URL?
    typealias BookmarkMaker = (URL) throws -> Data
    typealias BookmarkResolver = (Data) throws -> (url: URL, stale: Bool)
    typealias AccessStarter = (URL) -> Bool
    typealias AccessStopper = (URL) -> Void

    private let chooseDirectory: DirectoryChooser
    private let makeBookmark: BookmarkMaker
    private let resolveBookmark: BookmarkResolver
    private let startAccessing: AccessStarter
    private let stopAccessing: AccessStopper

    init(
        chooseDirectory: @escaping DirectoryChooser = ScopeResolver.openPanelDirectory,
        makeBookmark: @escaping BookmarkMaker = ScopeResolver.makeSecurityScopedBookmark,
        resolveBookmark: @escaping BookmarkResolver = ScopeResolver.resolveSecurityScopedBookmark,
        startAccessing: @escaping AccessStarter = { $0.startAccessingSecurityScopedResource() },
        stopAccessing: @escaping AccessStopper = { $0.stopAccessingSecurityScopedResource() }
    ) {
        self.chooseDirectory = chooseDirectory
        self.makeBookmark = makeBookmark
        self.resolveBookmark = resolveBookmark
        self.startAccessing = startAccessing
        self.stopAccessing = stopAccessing
    }

    nonisolated static func sameTrustedVolume(_ a: VolumeIdentity, _ b: VolumeIdentity, collision: Bool) -> Bool {
        guard !collision, let key = a.uuid, !key.isEmpty else { return false }
        return key == b.uuid && a.fileSystem == b.fileSystem && a.root == b.root
    }

    nonisolated static func validateChild(root: URL, file: URL) throws -> URL {
        let canonicalRoot = root.standardizedFileURL.resolvingSymlinksInPath()
        let canonicalFile = file.standardizedFileURL.resolvingSymlinksInPath()
        let rootComponents = canonicalRoot.pathComponents
        let fileComponents = canonicalFile.pathComponents
        guard fileComponents.count >= rootComponents.count,
              Array(fileComponents.prefix(rootComponents.count)) == rootComponents else {
            throw ScopeError.outsideAuthorizedDirectory
        }
        return canonicalFile
    }

    func authorizeDirectory() async throws -> URL {
        try await authorizeDirectoryWithBookmark().url
    }

    func authorizeDirectoryWithBookmark() async throws -> AuthorizedDirectory {
        guard let url = await chooseDirectory() else { throw ScopeError.selectionCancelled }
        do {
            return AuthorizedDirectory(url: url, bookmark: try makeBookmark(url))
        } catch let error as ScopeError {
            throw error
        } catch {
            throw ScopeError.invalidBookmark
        }
    }

    func restoreAccess(from bookmark: Data) throws -> SecurityScopedAccess {
        let resolved: (url: URL, stale: Bool)
        do { resolved = try resolveBookmark(bookmark) }
        catch let error as ScopeError { throw error }
        catch { throw ScopeError.invalidBookmark }
        guard !resolved.stale else { throw ScopeError.staleBookmark }
        guard startAccessing(resolved.url) else { throw ScopeError.accessDenied }
        return SecurityScopedAccess(url: resolved.url) { [stopAccessing] in stopAccessing(resolved.url) }
    }

    private static func openPanelDirectory() async -> URL? {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.canCreateDirectories = false
        panel.prompt = "授权文件夹"
        let response = await panel.begin()
        return response == .OK ? panel.url : nil
    }

    nonisolated private static func makeSecurityScopedBookmark(for url: URL) throws -> Data {
        try url.bookmarkData(
            options: [.withSecurityScope, .securityScopeAllowOnlyReadAccess],
            includingResourceValuesForKeys: nil,
            relativeTo: nil
        )
    }

    nonisolated private static func resolveSecurityScopedBookmark(_ data: Data) throws -> (url: URL, stale: Bool) {
        var stale = false
        let url = try URL(
            resolvingBookmarkData: data,
            options: .withSecurityScope,
            relativeTo: nil,
            bookmarkDataIsStale: &stale
        )
        return (url, stale)
    }
}
