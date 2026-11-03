// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "ScriptCore",
    platforms: [.iOS(.v18), .macOS(.v15)],
    products: [.library(name: "ScriptCore", targets: ["ScriptCore"])],
    targets: [
        .target(name: "ScriptCore"),
        .testTarget(name: "ScriptCoreTests", dependencies: ["ScriptCore"]),
    ]
)
