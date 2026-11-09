import Foundation
import ScriptCore
import UIKit

/// The device diagnostics: what this iPad and this build report about themselves, for the device test of
/// docs/DEVICE_TESTING.md. The file holds no participant code, no session identifier, and no recorded value.
struct DeviceDiagnostics {
    static let format = "script.diagnostics"
    static let version = "1.0"

    /// The folder in Files: On My iPad > SCRIPT Capture > Diagnostics.
    static func folder(documents: URL) -> URL { documents.appendingPathComponent("Diagnostics", isDirectory: true) }

    /// What the screen and the accessibility settings report. A test gives fixed values.
    struct Display {
        var widthPoints: Double
        var heightPoints: Double
        var scale: Double
        var maximumFramesPerSecond: Int
        var voiceOver: Bool
        var reduceMotion: Bool
        var contentSizeCategory: String

        @MainActor static func current() -> Display {
            let screen = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.first?.screen
            return Display(widthPoints: Double(screen?.bounds.width ?? 0), heightPoints: Double(screen?.bounds.height ?? 0),
                           scale: Double(screen?.scale ?? 0), maximumFramesPerSecond: screen?.maximumFramesPerSecond ?? 0,
                           voiceOver: UIAccessibility.isVoiceOverRunning, reduceMotion: UIAccessibility.isReduceMotionEnabled,
                           contentSizeCategory: UIApplication.shared.preferredContentSizeCategory.rawValue)
        }
    }

    /// The diagnostics as a JSON object with sorted keys.
    @MainActor static func document(store: SessionStore, display: Display, at: WallTime, freeBytes: Int64?) -> [String: Any] {
        let device = store.device
        let density = DeviceTable.density(model: device.model)
        var scale: [String: Any] = ["inDeviceTable": density != nil, "ppiConfirmed": density?.ppiConfirmed ?? false]
        if let density { scale["ppi"] = density.ppi }
        if let mm = device.tableScale?.mmPerPoint { scale["mmPerPoint"] = mm }
        let configuration = store.activeConfiguration
        var statuses: [String: Int] = [:]
        for row in store.listing { statuses[String(describing: row.status), default: 0] += 1 }
        var secret: [String: Any] = [:]
        if let accessible = store.storage.deviceSecret.accessibility() { secret["accessible"] = accessible }
        var storage: [String: Any] = ["items": store.storage.itemFacts().map { fact in
            var item: [String: Any] = ["name": fact.name, "exists": fact.exists]
            if let protection = fact.protection { item["protection"] = protection }
            if let excluded = fact.excludedFromBackup { item["excludedFromBackup"] = excluded }
            return item
        }]
        if let freeBytes { storage["freeBytes"] = freeBytes }
        return [
            "format": format, "version": version, "at": at.text,
            "app": ["version": device.app.version, "build": device.app.build, "sourceRevision": device.app.sourceRevision],
            "device": ["model": device.model, "os": device.os, "nativeScale": device.nativeScale,
                       "input": device.input.rawValue, "scale": scale],
            "display": ["widthPoints": display.widthPoints, "heightPoints": display.heightPoints, "scale": display.scale,
                        "maximumFramesPerSecond": display.maximumFramesPerSecond],
            "accessibility": ["voiceOver": display.voiceOver, "reduceMotion": display.reduceMotion,
                              "contentSizeCategory": display.contentSizeCategory],
            "storage": storage,
            "deviceSecret": secret,
            "configuration": ["study": configuration.study.id.rawValue, "version": configuration.study.version,
                              "sha256": configuration.sha256.hex, "demo": configuration.demo,
                              "operatorPin": configuration.operatorPin != nil],
            "sessions": statuses,
        ]
    }

    /// Writes the diagnostics file and returns its name. The folder has the protection and the backup exclusion of
    /// the exports.
    @MainActor static func save(store: SessionStore, display: Display, at: WallTime = Clock.now,
                                freeBytes: Int64?) throws -> String {
        let document = document(store: store, display: display, at: at, freeBytes: freeBytes)
        let data = try JSONSerialization.data(withJSONObject: document, options: [.prettyPrinted, .sortedKeys])
        let folder = folder(documents: store.storage.exports.deletingLastPathComponent())
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        var excluded = URLResourceValues()
        excluded.isExcludedFromBackup = true
        var target = folder
        try target.setResourceValues(excluded)
        let stamp = at.text.filter { $0.isLetter || $0.isNumber }
        let name = "SCRIPT_DIAGNOSTICS_\(stamp).json"
        try data.write(to: folder.appendingPathComponent(name), options: .atomic)
        return name
    }
}
