import Foundation

struct ScanTask: Identifiable, Codable, Equatable, Sendable {
    let id: UUID
    var name: String
}
