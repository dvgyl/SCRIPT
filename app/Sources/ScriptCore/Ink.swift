import Foundation

/// The Pencil model the operator declares. No public API reports it.
public enum PencilModel: String, Hashable, Sendable, CaseIterable {
    case pro, second, usbC, none
}

/// The planned channel set. QC compares the plan with the data.
public struct ChannelPlan: Hashable, Sendable {
    public var force: Bool
    public var altitude: Bool
    public var azimuth: Bool
    public var roll: Bool
    public var hover: Bool

    public init(force: Bool, altitude: Bool, azimuth: Bool, roll: Bool, hover: Bool) {
        self.force = force
        self.altitude = altitude
        self.azimuth = azimuth
        self.roll = roll
        self.hover = hover
    }

    /// The channels of the declared Pencil. USB-C has no force channel until a device test confirms what it reports
    /// (docs/DESIGN.md section 12). `none` is direct touch in the simulator, which has no Pencil channel.
    public static func plan(pencil: PencilModel, hoverSupported: Bool) -> ChannelPlan {
        switch pencil {
        case .pro: ChannelPlan(force: true, altitude: true, azimuth: true, roll: true, hover: hoverSupported)
        case .second: ChannelPlan(force: true, altitude: true, azimuth: true, roll: false, hover: hoverSupported)
        case .usbC: ChannelPlan(force: false, altitude: true, azimuth: true, roll: false, hover: hoverSupported)
        case .none: ChannelPlan(force: false, altitude: false, azimuth: false, roll: false, hover: false)
        }
    }

    /// Location and every planned pen channel, as `est` and `expect` bits.
    var properties: TouchProperties {
        var set: TouchProperties = .location
        if force { set.insert(.force) }
        if altitude { set.insert(.altitude) }
        if azimuth { set.insert(.azimuth) }
        if roll { set.insert(.roll) }
        return set
    }
}

/// Identifies one `UITouch`. The app builds it from the touch object identity.
public struct TouchKey: Hashable, Sendable {
    public let rawValue: UInt
    public init(_ rawValue: UInt) { self.rawValue = rawValue }
}

/// One coalesced Pencil touch, in view points and native UIKit units.
public struct PenReading: Sendable {
    public var key: TouchKey
    public var t: Double
    public var x: Double
    public var y: Double
    public var force: Double?
    public var maxForce: Double?
    public var altitude: Double?
    public var azimuth: Double?
    public var roll: Double?
    public var phase: PenPhase
    public var estimated: TouchProperties
    public var expectingUpdates: TouchProperties
    public var update: UpdateIndex?

    public init(
        key: TouchKey, t: Double, x: Double, y: Double, force: Double? = nil, maxForce: Double? = nil,
        altitude: Double? = nil, azimuth: Double? = nil, roll: Double? = nil, phase: PenPhase,
        estimated: TouchProperties = [], expectingUpdates: TouchProperties = [], update: UpdateIndex? = nil
    ) {
        self.key = key
        self.t = t
        self.x = x
        self.y = y
        self.force = force
        self.maxForce = maxForce
        self.altitude = altitude
        self.azimuth = azimuth
        self.roll = roll
        self.phase = phase
        self.estimated = estimated
        self.expectingUpdates = expectingUpdates
        self.update = update
    }
}

/// A predicted point for the ink preview only. It has no time and no channels, so it cannot become a sample.
public struct PreviewPoint: Hashable, Sendable {
    public var x: Double
    public var y: Double
    public init(x: Double, y: Double) {
        self.x = x
        self.y = y
    }
}

public struct HoverReading: Sendable {
    public var t: Double
    public var x: Double
    public var y: Double
    /// 0 at contact, 1 at the maximum range.
    public var z: Double
    public var altitude: Double?
    public var azimuth: Double?
    public var roll: Double?

    public init(t: Double, x: Double, y: Double, z: Double, altitude: Double? = nil, azimuth: Double? = nil,
                roll: Double? = nil) {
        self.t = t
        self.x = x
        self.y = y
        self.z = z
        self.altitude = altitude
        self.azimuth = azimuth
        self.roll = roll
    }
}

public struct FingerReading: Sendable {
    public var t: Double
    public var phase: FingerPhase
    public var x: Double
    public var y: Double

    public init(t: Double, phase: FingerPhase, x: Double, y: Double) {
        self.t = t
        self.phase = phase
        self.x = x
        self.y = y
    }
}

/// Turns UIKit readings into journal events. It owns the contact numbers and the update map for the whole
/// session, so a correction that arrives after a lift or after the next trial starts still finds its contact.
///
/// The recorder is the boundary for UIKit values: a non-finite channel value becomes an absent channel, and a
/// reading with a non-finite time or position gives no event.
public struct InkRecorder: Sendable {
    public let plan: ChannelPlan
    public private(set) var nextContact: ContactID
    private var down: [TouchKey: ContactID] = [:]
    /// Every pending estimate of the session. Samples that share a key share one entry, so one correction applies
    /// to all of them.
    private var updates: [UpdateKey: (contact: ContactID, expect: TouchProperties)] = [:]

    private struct UpdateKey: Hashable {
        let touch: TouchKey
        let update: UpdateIndex
    }

    public init(plan: ChannelPlan, nextContact: ContactID = .first) {
        self.plan = plan
        self.nextContact = nextContact
    }

    /// A recorder for a session that continues after a relaunch. The earlier process's touches are gone.
    public init(resuming session: Session) {
        self.init(plan: session.opened.channels, nextContact: session.nextContact)
    }

    /// Events for coalesced Pencil touches. A touch that this recorder did not see begin gives no events.
    public mutating func pen(_ readings: [PenReading]) -> [Event] {
        var events: [Event] = []
        for r in readings where r.t.isFinite && r.x.isFinite && r.y.isFinite {
            let contact: ContactID
            if let known = down[r.key] {
                contact = known
            } else {
                guard r.phase == .began else { continue }
                contact = nextContact
                nextContact = contact.next
                down[r.key] = contact
                events.append(.contactBegan(ContactBegan(t: r.t, contact: contact,
                                                         maxForce: plan.force ? positive(r.maxForce) : nil)))
            }
            let expect = r.expectingUpdates.intersection(plan.properties)
            var pending: Pending?
            if let update = r.update, !expect.isEmpty {
                pending = Pending(expect: expect, update: update)
                let key = UpdateKey(touch: r.key, update: update)
                updates[key] = (contact, expect.union(updates[key]?.expect ?? []))
            }
            events.append(.sample(Sample(
                contact: contact, t: r.t, x: r.x, y: r.y,
                force: plan.force ? finite(r.force) : nil,
                altitude: plan.altitude ? finite(r.altitude) : nil,
                azimuth: plan.azimuth ? finite(r.azimuth) : nil,
                roll: plan.roll ? finite(r.roll) : nil,
                phase: r.phase, est: r.estimated.intersection(plan.properties), pending: pending
            )))
            if r.phase == .ended || r.phase == .cancelled {
                down[r.key] = nil
                events.append(.contactEnded(ContactEnded(t: r.t, contact: contact, cancelled: r.phase == .cancelled)))
            }
        }
        return events
    }

    /// Events for `touchesEstimatedPropertiesUpdated`. Each correction carries the values that this update resolved.
    /// A correction that matches no pending estimate has no contact.
    public mutating func estimatesUpdated(_ readings: [PenReading], arrived: Double) -> [Event] {
        var events: [Event] = []
        for r in readings {
            guard let update = r.update else { continue }
            let key = UpdateKey(touch: r.key, update: update)
            let known = updates[key]
            let expected = known?.expect ?? plan.properties
            let remaining = r.expectingUpdates.intersection(expected)
            let resolved = expected.subtracting(remaining)
            let location = resolved.contains(.location) && r.x.isFinite && r.y.isFinite
            events.append(.correction(Correction(
                contact: known?.contact, update: update, arrived: arrived,
                x: location ? r.x : nil, y: location ? r.y : nil,
                force: resolved.contains(.force) ? finite(r.force) : nil,
                altitude: resolved.contains(.altitude) ? finite(r.altitude) : nil,
                azimuth: resolved.contains(.azimuth) ? finite(r.azimuth) : nil,
                roll: resolved.contains(.roll) ? finite(r.roll) : nil,
                expect: remaining
            )))
            if let known { updates[key] = (known.contact, remaining) }
        }
        return events
    }

    /// Ends every contact that is down, for a trial that ends while the Pencil touches the screen.
    public mutating func cancelAll(t: Double) -> [Event] {
        let contacts = down.values.sorted()
        down.removeAll()
        return contacts.map { .contactEnded(ContactEnded(t: t, contact: $0, cancelled: true)) }
    }

    public func hover(_ r: HoverReading) -> Event? {
        guard plan.hover, r.t.isFinite, r.x.isFinite, r.y.isFinite, r.z.isFinite else { return nil }
        return .hover(Hover(
            t: r.t, clock: .callback, x: r.x, y: r.y, z: max(0, r.z),
            altitude: plan.altitude ? finite(r.altitude) : nil,
            azimuth: plan.azimuth ? finite(r.azimuth) : nil,
            roll: plan.roll ? finite(r.roll) : nil
        ))
    }

    public func finger(_ r: FingerReading) -> Event? {
        guard r.t.isFinite, r.x.isFinite, r.y.isFinite else { return nil }
        return .finger(Finger(t: r.t, phase: r.phase, x: r.x, y: r.y))
    }

    private func finite(_ value: Double?) -> Double? {
        value.flatMap { $0.isFinite ? $0 : nil }
    }

    private func positive(_ value: Double?) -> Double? {
        value.flatMap { $0.isFinite && $0 > 0 ? $0 : nil }
    }
}

// MARK: - JSON

extension ChannelPlan {
    init(_ f: Fields) throws(DecodeError) {
        self.init(force: try f.bool("force"), altitude: try f.bool("altitude"), azimuth: try f.bool("azimuth"),
                  roll: try f.bool("roll"), hover: try f.bool("hover"))
    }

    var json: JSON {
        .object([
            ("force", .bool(force)), ("altitude", .bool(altitude)), ("azimuth", .bool(azimuth)),
            ("roll", .bool(roll)), ("hover", .bool(hover)),
        ])
    }
}
