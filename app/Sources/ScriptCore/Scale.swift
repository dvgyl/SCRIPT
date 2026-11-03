import Foundation

/// The object that the operator uses to measure a bar of known length in points.
public enum ReferenceObject: String, Hashable, Sendable {
    case steelRule
    /// An ISO/IEC 7810 ID-1 card, 85.60 mm wide.
    case idCard

    public static let idCardWidthMM = 85.60
}

/// How many millimetres one journal coordinate unit covers: a view point on the iPad, a tablet line on a tablet. An
/// unknown scale makes the library refuse millimetre features.
public enum MillimetreScale: Hashable, Sendable {
    case modelTable(mmPerPoint: Double, ppi: Double, nativeScale: Double)
    case reference(object: ReferenceObject, mmPerPoint: Double, lengthMM: Double, drawnPoints: Double)
    /// The published tablet resolution. `mmPerPoint = 1 / linesPerMM`.
    case tablet(linesPerMM: Double, mmPerPoint: Double)
    case unknown

    public var mmPerPoint: Double? {
        switch self {
        case .modelTable(let mm, _, _), .reference(_, let mm, _, _), .tablet(_, let mm): mm
        case .unknown: nil
        }
    }

    /// `mmPerPoint = 25.4 × nativeScale / ppi`. Nil when the device table has no entry for the model.
    public static func lookup(model: String, nativeScale: Double) -> MillimetreScale? {
        guard let entry = DeviceTable.density(model: model), nativeScale.isFinite, nativeScale > 0 else { return nil }
        return .modelTable(mmPerPoint: 25.4 * nativeScale / entry.ppi, ppi: entry.ppi, nativeScale: nativeScale)
    }

    /// The scale of a tablet with `linesPerMM` lines per millimetre. Nil unless the value is finite and positive.
    public static func tablet(linesPerMM: Double) -> MillimetreScale? {
        guard linesPerMM.isFinite, linesPerMM > 0 else { return nil }
        return .tablet(linesPerMM: linesPerMM, mmPerPoint: 1 / linesPerMM)
    }

    /// The operator measured a bar of `drawnPoints` with a steel rule and read `measuredMM`.
    public static func steelRule(measuredMM: Double, drawnPoints: Double) -> MillimetreScale? {
        reference(.steelRule, lengthMM: measuredMM, drawnPoints: drawnPoints)
    }

    /// The operator set a bar of `drawnPoints` to the width of an ID-1 card.
    public static func idCard(drawnPoints: Double) -> MillimetreScale? {
        reference(.idCard, lengthMM: ReferenceObject.idCardWidthMM, drawnPoints: drawnPoints)
    }

    private static func reference(_ object: ReferenceObject, lengthMM: Double, drawnPoints: Double) -> MillimetreScale? {
        guard lengthMM.isFinite, drawnPoints.isFinite, lengthMM > 0, drawnPoints > 0 else { return nil }
        return .reference(object: object, mmPerPoint: lengthMM / drawnPoints, lengthMM: lengthMM, drawnPoints: drawnPoints)
    }
}

/// The physical pixel density of one iPad model.
public struct PixelDensity: Hashable, Sendable {
    /// The `hw.machine` identifier, for example `iPad16,3`.
    public let model: String
    public let name: String
    public let ppi: Double
    /// False when no source confirmed the ppi value; Setup then asks for a reference measurement. On 2026-11-03
    /// every ppi came from Apple's tech-specs page for the model, and every identifier from both the EveryMac
    /// identifier list and AppleDB. A model whose identifier only one source gives is left out of the table.
    public let ppiConfirmed: Bool

    public init(model: String, name: String, ppi: Double, ppiConfirmed: Bool) {
        self.model = model
        self.name = name
        self.ppi = ppi
        self.ppiConfirmed = ppiConfirmed
    }
}

public enum DeviceTable {
    public static let entries: [PixelDensity] = [
        PixelDensity(model: "iPad16,3", name: "iPad Pro 11-inch (M4) Wi-Fi", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad16,4", name: "iPad Pro 11-inch (M4) cellular", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad16,5", name: "iPad Pro 13-inch (M4) Wi-Fi", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad16,6", name: "iPad Pro 13-inch (M4) cellular", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad17,1", name: "iPad Pro 11-inch (M5) Wi-Fi", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad17,2", name: "iPad Pro 11-inch (M5) cellular", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad17,3", name: "iPad Pro 13-inch (M5) Wi-Fi", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad17,4", name: "iPad Pro 13-inch (M5) cellular", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad15,3", name: "iPad Air 11-inch (M3) Wi-Fi", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad15,4", name: "iPad Air 11-inch (M3) cellular", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad15,5", name: "iPad Air 13-inch (M3) Wi-Fi", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad15,6", name: "iPad Air 13-inch (M3) cellular", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad16,8", name: "iPad Air 11-inch (M4) Wi-Fi", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad16,9", name: "iPad Air 11-inch (M4) cellular", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad16,10", name: "iPad Air 13-inch (M4) Wi-Fi", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad16,11", name: "iPad Air 13-inch (M4) cellular", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad16,1", name: "iPad mini (A17 Pro) Wi-Fi", ppi: 326, ppiConfirmed: true),
        PixelDensity(model: "iPad16,2", name: "iPad mini (A17 Pro) cellular", ppi: 326, ppiConfirmed: true),
        PixelDensity(model: "iPad15,7", name: "iPad (A16) Wi-Fi", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad15,8", name: "iPad (A16) cellular", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad14,8", name: "iPad Air 11-inch (M2) Wi-Fi", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad14,9", name: "iPad Air 11-inch (M2) cellular", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad14,10", name: "iPad Air 13-inch (M2) Wi-Fi", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad14,11", name: "iPad Air 13-inch (M2) cellular", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad14,3", name: "iPad Pro 11-inch (4th generation) Wi-Fi", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad14,4", name: "iPad Pro 11-inch (4th generation) cellular", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad14,5", name: "iPad Pro 12.9-inch (6th generation) Wi-Fi", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad14,6", name: "iPad Pro 12.9-inch (6th generation) cellular", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad13,4", name: "iPad Pro 11-inch (3rd generation) Wi-Fi", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad13,5", name: "iPad Pro 11-inch (3rd generation) Wi-Fi", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad13,6", name: "iPad Pro 11-inch (3rd generation) cellular", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad13,7", name: "iPad Pro 11-inch (3rd generation) cellular", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad13,8", name: "iPad Pro 12.9-inch (5th generation) Wi-Fi", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad13,9", name: "iPad Pro 12.9-inch (5th generation) Wi-Fi", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad13,10", name: "iPad Pro 12.9-inch (5th generation) cellular", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad13,11", name: "iPad Pro 12.9-inch (5th generation) cellular", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad8,9", name: "iPad Pro 11-inch (2nd generation) Wi-Fi", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad8,10", name: "iPad Pro 11-inch (2nd generation) cellular", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad8,11", name: "iPad Pro 12.9-inch (4th generation) Wi-Fi", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad8,12", name: "iPad Pro 12.9-inch (4th generation) cellular", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad13,16", name: "iPad Air (5th generation) Wi-Fi", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad13,17", name: "iPad Air (5th generation) cellular", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad13,1", name: "iPad Air (4th generation) Wi-Fi", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad13,2", name: "iPad Air (4th generation) cellular", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad13,18", name: "iPad (10th generation) Wi-Fi", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad13,19", name: "iPad (10th generation) cellular", ppi: 264, ppiConfirmed: true),
        PixelDensity(model: "iPad14,1", name: "iPad mini (6th generation) Wi-Fi", ppi: 326, ppiConfirmed: true),
        PixelDensity(model: "iPad14,2", name: "iPad mini (6th generation) cellular", ppi: 326, ppiConfirmed: true),
    ]

    private static let byModel = Dictionary(uniqueKeysWithValues: entries.map { ($0.model, $0) })

    public static func density(model: String) -> PixelDensity? { byModel[model] }
}

/// The geometry of one graphics tablet model: its resolution, its active area, and the direction of its y axis.
public struct TabletGeometry: Hashable, Sendable {
    /// The model text that `tablet.model` records, for example `Wacom Intuos Pro Medium (2025)`.
    public let model: String
    public let productCode: String
    public let linesPerMM: Double
    /// The active area in tablet lines, which a tablet trial records as `view`.
    public let activeAreaLines: ViewSize
    /// True when `absoluteY` grows downwards. The capture flips y when this is false, so every journal keeps the
    /// one rule "origin top-left, y down".
    public let yDown: Bool
    /// False when no manufacturer source confirmed the resolution; the operator then measures a reference.
    public let confirmed: Bool

    public init(model: String, productCode: String, linesPerMM: Double, activeAreaLines: ViewSize, yDown: Bool,
                confirmed: Bool) {
        self.model = model
        self.productCode = productCode
        self.linesPerMM = linesPerMM
        self.activeAreaLines = activeAreaLines
        self.yDown = yDown
        self.confirmed = confirmed
    }

    public var scale: MillimetreScale { MillimetreScale.tablet(linesPerMM: linesPerMM)! }
}

public enum TabletTable {
    public static let entries: [TabletGeometry] = [
        // 200 lines per mm (5080 lpi) and the 263 x 148 mm active area come from the Wacom product page that the
        // bench protocol cites as W1. `yDown` is provisional until the device test confirms the direction of
        // `absoluteY`, which Apple does not document.
        TabletGeometry(model: "Wacom Intuos Pro Medium (2025)", productCode: "PTK670", linesPerMM: 200,
                       activeAreaLines: ViewSize(width: 52600, height: 29600)!, yDown: false, confirmed: true),
    ]

    private static let byModel = Dictionary(uniqueKeysWithValues: entries.map { ($0.model, $0) })

    public static func geometry(model: String) -> TabletGeometry? { byModel[model] }
}

// MARK: - JSON

extension MillimetreScale {
    init(_ f: Fields) throws(DecodeError) {
        switch try f.string("kind") {
        case "modelTable":
            self = .modelTable(mmPerPoint: try f.positive("mmPerPoint"), ppi: try f.positive("ppi"),
                               nativeScale: try f.positive("nativeScale"))
        case "reference":
            self = .reference(object: try f.enumeration("object"), mmPerPoint: try f.positive("mmPerPoint"),
                              lengthMM: try f.positive("lengthMM"), drawnPoints: try f.positive("drawnPoints"))
        case "tablet":
            self = .tablet(linesPerMM: try f.positive("linesPerMM"), mmPerPoint: try f.positive("mmPerPoint"))
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
        case .reference(let object, let mm, let length, let drawn):
            o.add("kind", "reference")
            o.add("object", object.rawValue)
            try o.add("mmPerPoint", mm)
            try o.add("lengthMM", length)
            try o.add("drawnPoints", drawn)
        case .tablet(let lines, let mm):
            o.add("kind", "tablet")
            try o.add("linesPerMM", lines)
            try o.add("mmPerPoint", mm)
        case .unknown:
            o.add("kind", "unknown")
        }
        return o.json
    }
}
