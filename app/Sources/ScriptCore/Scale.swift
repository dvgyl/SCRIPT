import Foundation

/// How many millimetres one view point covers. An unknown scale makes the library refuse millimetre features.
public enum MillimetreScale: Hashable, Sendable {
    case modelTable(mmPerPoint: Double, ppi: Double, nativeScale: Double)
    case ruler(mmPerPoint: Double, measuredMM: Double, drawnPoints: Double)
    case unknown

    public var mmPerPoint: Double? {
        switch self {
        case .modelTable(let mm, _, _), .ruler(let mm, _, _): mm
        case .unknown: nil
        }
    }

    /// `mmPerPoint = 25.4 × nativeScale / ppi`. Nil when the device table has no entry for the model.
    public static func lookup(model: String, nativeScale: Double) -> MillimetreScale? {
        guard let entry = DeviceTable.density(model: model), nativeScale.isFinite, nativeScale > 0 else { return nil }
        return .modelTable(mmPerPoint: 25.4 * nativeScale / entry.ppi, ppi: entry.ppi, nativeScale: nativeScale)
    }

    /// The operator measured a bar of `drawnPoints` with a steel rule and read `measuredMM`.
    public static func calibrated(measuredMM: Double, drawnPoints: Double) -> MillimetreScale? {
        guard measuredMM.isFinite, drawnPoints.isFinite, measuredMM > 0, drawnPoints > 0 else { return nil }
        return .ruler(mmPerPoint: measuredMM / drawnPoints, measuredMM: measuredMM, drawnPoints: drawnPoints)
    }
}

/// The physical pixel density of one iPad model.
public struct PixelDensity: Hashable, Sendable {
    /// The `hw.machine` identifier, for example `iPad16,3`.
    public let model: String
    public let name: String
    public let ppi: Double
    /// False when no source checked for this table confirmed the ppi value. The identifiers come from the
    /// EveryMac identifier list (checked 2026-11-03). Setup can ask for a ruler calibration when this is false.
    public let ppiConfirmed: Bool
}

public enum DeviceTable {
    public static let entries: [PixelDensity] = [
        PixelDensity(model: "iPad16,3", name: "iPad Pro 11-inch (M4) Wi-Fi", ppi: 264, ppiConfirmed: false),
        PixelDensity(model: "iPad16,4", name: "iPad Pro 11-inch (M4) cellular", ppi: 264, ppiConfirmed: false),
        PixelDensity(model: "iPad16,5", name: "iPad Pro 13-inch (M4) Wi-Fi", ppi: 264, ppiConfirmed: false),
        PixelDensity(model: "iPad16,6", name: "iPad Pro 13-inch (M4) cellular", ppi: 264, ppiConfirmed: false),
        PixelDensity(model: "iPad17,1", name: "iPad Pro 11-inch (M5) Wi-Fi", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad17,2", name: "iPad Pro 11-inch (M5) cellular", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad17,3", name: "iPad Pro 13-inch (M5) Wi-Fi", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad17,4", name: "iPad Pro 13-inch (M5) cellular", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad15,3", name: "iPad Air 11-inch (M3) Wi-Fi", ppi: 264, ppiConfirmed: false),
        PixelDensity(model: "iPad15,4", name: "iPad Air 11-inch (M3) cellular", ppi: 264, ppiConfirmed: false),
        PixelDensity(model: "iPad15,5", name: "iPad Air 13-inch (M3) Wi-Fi", ppi: 264, ppiConfirmed: false),
        PixelDensity(model: "iPad15,6", name: "iPad Air 13-inch (M3) cellular", ppi: 264, ppiConfirmed: false),
        PixelDensity(model: "iPad16,8", name: "iPad Air 11-inch (M4) Wi-Fi", ppi: 264, ppiConfirmed: false),
        PixelDensity(model: "iPad16,9", name: "iPad Air 11-inch (M4) cellular", ppi: 264, ppiConfirmed: false),
        PixelDensity(model: "iPad16,10", name: "iPad Air 13-inch (M4) Wi-Fi", ppi: 264, ppiConfirmed: false),
        PixelDensity(model: "iPad16,11", name: "iPad Air 13-inch (M4) cellular", ppi: 264, ppiConfirmed: false),
        PixelDensity(model: "iPad16,1", name: "iPad mini (A17 Pro) Wi-Fi", ppi: 326, ppiConfirmed: true),
        PixelDensity(model: "iPad16,2", name: "iPad mini (A17 Pro) cellular", ppi: 326, ppiConfirmed: true),
        PixelDensity(model: "iPad15,7", name: "iPad (A16) Wi-Fi", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad15,8", name: "iPad (A16) cellular", ppi: 264, ppiConfirmed: true),
    ]

    private static let byModel = Dictionary(uniqueKeysWithValues: entries.map { ($0.model, $0) })

    public static func density(model: String) -> PixelDensity? { byModel[model] }
}

// MARK: - JSON

extension MillimetreScale {
    init(_ f: Fields) throws(LineCodec.DecodeError) {
        switch try f.string("kind") {
        case "modelTable":
            self = .modelTable(mmPerPoint: try f.positive("mmPerPoint"), ppi: try f.positive("ppi"),
                               nativeScale: try f.positive("nativeScale"))
        case "ruler":
            self = .ruler(mmPerPoint: try f.positive("mmPerPoint"), measuredMM: try f.positive("measuredMM"),
                          drawnPoints: try f.positive("drawnPoints"))
        case "unknown":
            self = .unknown
        default:
            throw .invalid(f.keyPath("kind"))
        }
    }

    func json() throws(LineCodec.EncodeError) -> JSON {
        var o = ObjectBuilder()
        switch self {
        case .modelTable(let mm, let ppi, let nativeScale):
            o.add("kind", "modelTable")
            try o.add("mmPerPoint", mm)
            try o.add("ppi", ppi)
            try o.add("nativeScale", nativeScale)
        case .ruler(let mm, let measured, let drawn):
            o.add("kind", "ruler")
            try o.add("mmPerPoint", mm)
            try o.add("measuredMM", measured)
            try o.add("drawnPoints", drawn)
        case .unknown:
            o.add("kind", "unknown")
        }
        return o.json
    }
}
