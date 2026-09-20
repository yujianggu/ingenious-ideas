import Foundation

struct VolumeIdentity: Equatable, Codable, Sendable {
    let uuid: String?
    let fileSystem: String
    let root: URL
}
