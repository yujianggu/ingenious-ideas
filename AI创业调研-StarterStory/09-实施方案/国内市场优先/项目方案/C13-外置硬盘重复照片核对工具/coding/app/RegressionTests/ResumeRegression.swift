import Foundation
@main struct ResumeRegression {
    static func main() async {
        var passed = 0
        let scenarios = CommandLine.arguments.contains("--quick") ? ResumeScenarios.all.filter { $0.0 != "100001 real file quota" } : ResumeScenarios.all
        for (name, test) in scenarios {
            do { try await test(); passed += 1; print("PASS \(name)") }
            catch { print("FAIL \(name): \(error)") }
        }
        print("Resume regression: \(passed)/\(scenarios.count) passed")
        if passed != scenarios.count { exit(1) }
    }
}
