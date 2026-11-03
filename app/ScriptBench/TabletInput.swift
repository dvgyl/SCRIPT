import AppKit
import ScriptCore

/// The fields of one AppKit event that the capture reads. `TabletCaptureView.swift` copies them from `NSEvent`, so the
/// mapping below needs no AppKit event.
enum TabletEvent: Hashable, Sendable {
    /// A pen report: a mouse event with the tablet-point subtype, or a tablet-point event.
    case point(TabletPoint)
    /// A pointing device enters or leaves the tablet's range.
    case proximity(TabletProximity)
    /// A plain mouse or trackpad event.
    case other

    /// `NSEvent.timestamp`, or nil for an event that is not from the tablet.
    var t: Double? {
        switch self {
        case .point(let p): p.t
        case .proximity(let p): p.t
        case .other: nil
        }
    }

    /// What an AppKit event carries, from its type and subtype.
    enum Kind: Hashable, Sendable {
        case point(TabletPoint.Source)
        case proximity
        case other
    }

    /// AppKit documents `subtype` for mouse events only, so `subtype` is read for the four mouse event types alone.
    static func kind(type: NSEvent.EventType, subtype: () -> NSEvent.EventSubtype) -> Kind {
        let source: TabletPoint.Source
        switch type {
        case .tabletProximity: return .proximity
        case .tabletPoint: return .point(.tabletPoint)
        case .leftMouseDown: source = .down
        case .leftMouseDragged: source = .dragged
        case .leftMouseUp: source = .up
        case .mouseMoved: source = .moved
        default: return .other
        }
        switch subtype() {
        case .tabletPoint: return .point(source)
        case .tabletProximity: return .proximity
        default: return .other
        }
    }
}

struct TabletPoint: Hashable, Sendable {
    /// The AppKit event that carried the report.
    enum Source: Hashable, Sendable { case down, dragged, up, moved, tabletPoint }

    var source: Source
    var t: Double
    var device: Int
    var absoluteX: Int
    var absoluteY: Int
    var pressure: Double
    var tiltX: Double
    var tiltY: Double
    var rotation: Double
}

struct TabletProximity: Hashable, Sendable {
    var t: Double
    var device: Int
    var entering: Bool
    var pointingDevice: NSEvent.PointingDeviceType
}

/// What one tablet event gives the session.
enum TabletReading {
    case pen(PenReading)
    case hover(HoverReading)
    /// The pen left the tablet's range during a contact, which then ends as cancelled.
    case left(t: Double)
}

/// Turns tablet events into pen and hover readings in journal coordinates: tablet lines, origin top-left, y down
/// (bench protocol section 2.4).
struct TabletMapper {
    let geometry: TabletGeometry
    /// The devices whose latest proximity event named no pen. Every event of such a device is ignored.
    private var ignored: Set<Int> = []
    /// The device whose contact is open.
    private var contact: Int?
    /// The latest hover position, which Set origin stores. Nil while no pen is in range.
    private(set) var hover: Point?

    init(geometry: TabletGeometry) { self.geometry = geometry }

    mutating func reading(_ event: TabletEvent) -> TabletReading? {
        switch event {
        case .other:
            return nil
        case .proximity(let p):
            guard p.pointingDevice == .pen else {
                ignored.insert(p.device)
                return nil
            }
            ignored.remove(p.device)
            guard !p.entering else { return nil }
            hover = nil
            guard contact == p.device else { return nil }
            contact = nil
            return .left(t: p.t)
        case .point(let p):
            guard !ignored.contains(p.device) else { return nil }
            let at = position(p)
            switch (p.source, contact == p.device) {
            case (.down, _):
                contact = p.device
                return .pen(reading(p, at, .began))
            case (.dragged, true):
                return .pen(reading(p, at, .moved))
            case (.tabletPoint, true):
                return .pen(reading(p, at, .stationary))
            case (.up, true):
                contact = nil
                return .pen(reading(p, at, .ended))
            case (.moved, false):
                hover = at
                return .hover(HoverReading(t: p.t, clock: .event, x: at.x, y: at.y, tiltX: p.tiltX, tiltY: p.tiltY,
                                           rotation: p.rotation))
            default:
                // A tablet-point event without a contact repeats the report of a mouse-moved event, so only the
                // mouse-moved event gives the hover line.
                return nil
            }
        }
    }

    /// AppKit does not document the direction of `absoluteY`, so the table records it for each model.
    private func position(_ p: TabletPoint) -> Point {
        let y = Double(p.absoluteY)
        return Point(x: Double(p.absoluteX), y: geometry.yDown ? y : geometry.activeAreaLines.height - y)
    }

    /// `pressure` is normalized, so `maxForce` is 1. A tablet has no estimated property.
    private func reading(_ p: TabletPoint, _ at: Point, _ phase: PenPhase) -> PenReading {
        PenReading(key: TouchKey(UInt(truncatingIfNeeded: p.device)), t: p.t, x: at.x, y: at.y, force: p.pressure,
                   maxForce: 1, tiltX: p.tiltX, tiltY: p.tiltY, rotation: p.rotation, phase: phase)
    }
}

/// The launch check that the tablet's event clock is the uptime clock of the app events (proposal section 4.7).
/// Apple documents both as seconds since system startup, but no page says that they share one clock.
enum ClockCheck: Hashable, Sendable {
    /// No tablet event has arrived since launch.
    case waiting
    case passed
    /// `difference` is the uptime minus the event time, in seconds.
    case failed(difference: Double)

    static let tolerance = 1.0

    /// The result for one tablet event: its `NSEvent.timestamp` and the `systemUptime` read in its handler.
    init(eventTime: Double, uptime: Double) {
        let difference = uptime - eventTime
        self = abs(difference) <= Self.tolerance ? .passed : .failed(difference: difference)
    }
}
