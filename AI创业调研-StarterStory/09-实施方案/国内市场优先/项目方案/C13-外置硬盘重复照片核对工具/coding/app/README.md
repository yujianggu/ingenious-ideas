# PhotoEvidence macOS app

The checked-in Xcode project has the `PhotoEvidence`, `PhotoEvidenceTests`, and `PhotoEvidenceUITests` targets and a shared `PhotoEvidence` scheme. The deployment target is macOS 13 and the placeholder bundle identifier is `cn.ingeniousideas.photoevidence`.

This machine has Apple Swift 6.2.3 and the macOS 26.2 Command Line Tools SDK, but no full Xcode installation. Build the native SwiftUI bundle and the core regression executable with:

```sh
./scripts/build.sh
./build/bin/task-store-regression
```

The regression executable compiles the same `Models.swift` and `TaskStore.swift` files as the app target. It does not copy the persistence implementation.

The current environment cannot run XCTest, XCUITest, signed archives, notarization, or GUI interaction checks. Those remain pending until full Xcode, signing credentials, and a graphical test session are available.

Task 4 adds `build/bin/resume-regression` (shared production-core scenarios, including a real 100,001-file quota test; `--quick` skips only that case). Queue, checkpoint, progress, history and Task 5/6 integration contracts are documented in [COORDINATOR_API.md](COORDINATOR_API.md). The queue is not yet wired to the Task 6 UI.
