#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
build_dir="$project_dir/build"
app_dir="$build_dir/PhotoEvidence.app"
binary_dir="$app_dir/Contents/MacOS"
resources_dir="$app_dir/Contents/Resources"

rm -rf "$app_dir"
mkdir -p "$binary_dir" "$resources_dir" "$build_dir/bin"

xcrun swiftc \
  -swift-version 6 \
  -parse-as-library \
  -target x86_64-apple-macosx13.0 \
  -framework SwiftUI \
  -framework AppKit \
  -lsqlite3 \
  "$project_dir/PhotoEvidence/Domain/Models.swift" \
  "$project_dir/PhotoEvidence/Storage/TaskStore.swift" \
  "$project_dir/PhotoEvidence/Scanning/VolumeIdentity.swift" \
  "$project_dir/PhotoEvidence/Scanning/ScopeResolver.swift" \
  "$project_dir/PhotoEvidence/Scanning/FileVerifier.swift" \
  "$project_dir/PhotoEvidence/Storage/ObservationStore.swift" \
  "$project_dir/PhotoEvidence/Scanning/ScanCoordinator.swift" \
  "$project_dir/PhotoEvidence/PhotoEvidenceApp.swift" \
  -o "$binary_dir/PhotoEvidence"
cp "$project_dir/PhotoEvidence/Info.plist" "$app_dir/Contents/Info.plist"

xcrun swiftc \
  -swift-version 6 \
  -parse-as-library \
  -target x86_64-apple-macosx13.0 \
  -lsqlite3 \
  "$project_dir/PhotoEvidence/Domain/Models.swift" \
  "$project_dir/PhotoEvidence/Storage/TaskStore.swift" \
  "$project_dir/RegressionTests/TaskStoreRegression.swift" \
  -o "$build_dir/bin/task-store-regression"

xcrun swiftc \
  -swift-version 6 \
  -parse-as-library \
  -target x86_64-apple-macosx13.0 \
  -framework AppKit \
  "$project_dir/PhotoEvidence/Scanning/VolumeIdentity.swift" \
  "$project_dir/PhotoEvidence/Scanning/ScopeResolver.swift" \
  "$project_dir/RegressionTests/ScopeRegression.swift" \
  -o "$build_dir/bin/scope-regression"

xcrun swiftc \
  -swift-version 6 \
  -parse-as-library \
  -target x86_64-apple-macosx13.0 \
  -framework AppKit \
  "$project_dir/PhotoEvidence/Scanning/VolumeIdentity.swift" \
  "$project_dir/PhotoEvidence/Scanning/ScopeResolver.swift" \
  "$project_dir/PhotoEvidence/Scanning/FileVerifier.swift" \
  "$project_dir/RegressionTests/FileVerifierRegression.swift" \
  -o "$build_dir/bin/file-verifier-regression"

echo "Built $app_dir"
echo "Built $build_dir/bin/task-store-regression"
echo "Built $build_dir/bin/scope-regression"
echo "Built $build_dir/bin/file-verifier-regression"

xcrun swiftc \
  -swift-version 6 \
  -parse-as-library \
  -target x86_64-apple-macosx13.0 \
  -framework AppKit -lsqlite3 \
  "$project_dir/PhotoEvidence/Domain/Models.swift" \
  "$project_dir/PhotoEvidence/Storage/TaskStore.swift" \
  "$project_dir/PhotoEvidence/Storage/ObservationStore.swift" \
  "$project_dir/PhotoEvidence/Scanning/VolumeIdentity.swift" \
  "$project_dir/PhotoEvidence/Scanning/ScopeResolver.swift" \
  "$project_dir/PhotoEvidence/Scanning/FileVerifier.swift" \
  "$project_dir/PhotoEvidence/Scanning/ScanCoordinator.swift" \
  "$project_dir/TestSupport/ResumeScenarios.swift" \
  "$project_dir/RegressionTests/ResumeRegression.swift" \
  -o "$build_dir/bin/resume-regression"
echo "Built $build_dir/bin/resume-regression"
