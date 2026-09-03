// swift-tools-version: 5.10
// No dependencies, by design: Reel is native. See SPEC.md.
import PackageDescription

let package = Package(
    name: "Reel",
    platforms: [.macOS(.v14)],
    products: [
        .library(name: "ReelKit", targets: ["ReelKit"]),
        .executable(name: "Reel", targets: ["Reel"]),
    ],
    targets: [
        .target(name: "ReelKit"),
        .executableTarget(name: "Reel", dependencies: ["ReelKit"]),
        .testTarget(name: "ReelKitTests", dependencies: ["ReelKit"]),
    ]
)
