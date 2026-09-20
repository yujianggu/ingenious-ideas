import XCTest
@testable import PhotoEvidence
final class ResumeTests: XCTestCase {
    func testRequiredBehavior() throws {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: url) }
        let task = UUID(), other = UUID()
        try ObservationStore(url: url).record(task: task, path: "a.jpg", complete: true, evidence: .hashMatch)
        try ObservationStore(url: url).record(task: task, path: "b.jpg", complete: false, evidence: .unknown)
        let reopened = try ObservationStore(url: url)
        XCTAssertEqual(try reopened.completed(task: task), ["a.jpg"])
        XCTAssertEqual(try reopened.completed(task: other), [])
    }
    func testScanAndHistory() async throws { try await ResumeScenarios.scanAndHistory() }
    func testCancellationAndResume() async throws { try await ResumeScenarios.cancellationAndResume() }
    func testDisconnectAndAuthorization() async throws { try await ResumeScenarios.disconnectAndAuthorization() }
    func testQuotas() async throws { try await ResumeScenarios.quotas() }
    func testConcurrency() async throws { try await ResumeScenarios.concurrency() }
    func testAtomicFailure() async throws { try await ResumeScenarios.atomicFailure() }
    func testResumeInterruptedEnumeration() async throws { try await ResumeScenarios.resumeInterruptedEnumeration() }
    func testSourceBoundaries() async throws { try await ResumeScenarios.sourceBoundaries() }
    func testIdentityAmbiguity() async throws { try await ResumeScenarios.identityAmbiguity() }
    func testRevisionsAndInvalidDigest() async throws { try await ResumeScenarios.revisionsAndInvalidDigest() }
    func testCapacity() async throws { try await ResumeScenarios.capacity() }
    func testRecheckHistoryAndAlgorithm() async throws { try await ResumeScenarios.recheckHistoryAndAlgorithm() }
    func testNestedVolumeExclusions() async throws { try await ResumeScenarios.nestedVolumeExclusions() }
    func testDescriptorDeviceReplacement() async throws { try await ResumeScenarios.deviceReplacementAtOpen() }
    func testNestedReplacementAtOpen() async throws { try await ResumeScenarios.nestedReplacementAtOpen() }
    func testSameLiveVolume() async throws { try await ResumeScenarios.sameLiveVolume() }
}
