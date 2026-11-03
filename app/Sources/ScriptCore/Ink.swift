import Foundation

/// The Pencil model the operator declares. No public API reports it.
public enum PencilModel: String, Hashable, Sendable, CaseIterable {
    case pro, second, usbC, none
}

/// The planned channel set. QC compares the plan with the data. A Pencil plans `altitude`, `azimuth`, and `roll`. A
/// tablet pen plans `tiltX`, `tiltY`, and `rotation`.
public struct ChannelPlan: Hashable, Sendable {
    public var force: Bool
    public var altitude: Bool
    public var azimuth: Bool
    public var roll: Bool
    public var hover: Bool
    public var tiltX: Bool
    public var tiltY: Bool
    public var rotation: Bool

    public init(force: Bool, altitude: Bool, azimuth: Bool, roll: Bool, hover: Bool, tiltX: Bool = false,
                tiltY: Bool = false, rotation: Bool = false) {
        self.force = force
        self.altitude = altitude
        self.azimuth = azimuth
        self.roll = roll
        self.hover = hover
        self.tiltX = tiltX
        self.tiltY = tiltY
        self.rotation = rotation
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

    /// The channels of the declared tablet pen. A pen in proximity gives hover lines on every tablet. Rotation is
    /// planned only for a pen that reports it, and the Pro Pen 3 does not.
    public static func plan(tabletPen pen: TabletPen) -> ChannelPlan {
        switch pen {
        case .proPen3: ChannelPlan(force: true, altitude: false, azimuth: false, roll: false, hover: true, tiltX: true, tiltY: true)
        case .unknown: ChannelPlan(force: true, altitude: false, azimuth: false, roll: false, hover: true)
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

/// One coalesced Pencil touch in view points and native UIKit units, or one tablet event in tablet lines and native
/// AppKit units. A tablet reading has no estimated properties and no update index.
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
    public var tiltX: Double?
    public var tiltY: Double?
    public var rotation: Double?
    public var phase: PenPhase
    public var estimated: TouchProperties
    public var expectingUpdates: TouchProperties
    public var update: UpdateIndex?

    public init(
        key: TouchKey, t: Double, x: Double, y: Double, force: Double? = nil, maxForce: Double? = nil,
        altitude: Double? = nil, azimuth: Double? = nil, roll: Double? = nil, tiltX: Double? = nil,
        tiltY: Double? = nil, rotation: Double? = nil, phase: PenPhase, estimated: TouchProperties = [],
        expectingUpdates: TouchProperties = [], update: UpdateIndex? = nil
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
        self.tiltX = tiltX
        self.tiltY = tiltY
        self.rotation = rotation
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
    /// `callback` for a UIKit hover recognizer, `event` for an AppKit tablet event.
    public var clock: HoverClock
    public var x: Double
    public var y: Double
    /// 0 at contact, 1 at the maximum range. Nil for a tablet, which reports no height.
    public var z: Double?
    public var altitude: Double?
    public var azimuth: Double?
    public var roll: Double?
    public var tiltX: Double?
    public var tiltY: Double?
    public var rotation: Double?

    public init(t: Double, clock: HoverClock = .callback, x: Double, y: Double, z: Double? = nil,
                altitude: Double? = nil, azimuth: Double? = nil, roll: Double? = nil, tiltX: Double? = nil,
                tiltY: Double? = nil, rotation: Double? = nil) {
        self.t = t
        self.clock = clock
        self.x = x
        self.y = y
        self.z = z
        self.altitude = altitude
        self.azimuth = azimuth
        self.roll = roll
        self.tiltX = tiltX
        self.tiltY = tiltY
        self.rotation = rotation
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
/// The recorder is the boundary for UIKit values. It gives no value that the line decoder refuses: a non-finite
/// channel value or a negative force becomes an absent channel, and a reading with a negative or non-finite time
/// or a non-finite position gives no sample.
public struct InkRecorder: Sendable {
    public let plan: ChannelPlan
    public private(set) var nextContact: ContactID
    private var down: [TouchKey: OpenContact] = [:]
    /// Every pending estimate of the session. Samples that share a key share one entry, so one correction applies
    /// to all of them.
    private var updates: [UpdateKey: (contact: ContactID, expect: TouchProperties)] = [:]

    private struct UpdateKey: Hashable {
        let touch: TouchKey
        let update: UpdateIndex
    }

    private struct OpenContact {
        let contact: ContactID
        /// The time of the contact's last sample.
        var t: Double
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
    ///
    /// A contact always ends, so two strokes never share one: a `began` for a key that is down ends the old contact
    /// as cancelled, because UIKit reuses `UITouch` objects, and a lift reading that gives no sample still ends its
    /// contact at the time of the last sample.
    public mutating func pen(_ readings: [PenReading]) -> [Event] {
        var events: [Event] = []
        for r in readings {
            let lifts = r.phase == .ended || r.phase == .cancelled
            if r.phase == .began, let stale = down.removeValue(forKey: r.key) {
                events.append(.contactEnded(ContactEnded(t: stale.t, contact: stale.contact, cancelled: true)))
            }
            guard isUptime(r.t), r.x.isFinite, r.y.isFinite else {
                if lifts, let open = down.removeValue(forKey: r.key) {
                    events.append(.contactEnded(ContactEnded(t: open.t, contact: open.contact,
                                                             cancelled: r.phase == .cancelled)))
                }
                continue
            }
            let contact: ContactID
            if let open = down[r.key] {
                contact = open.contact
            } else {
                guard r.phase == .began else { continue }
                contact = nextContact
                nextContact = contact.next
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
                force: plan.force ? force(r.force) : nil,
                altitude: plan.altitude ? finite(r.altitude) : nil,
                azimuth: plan.azimuth ? finite(r.azimuth) : nil,
                roll: plan.roll ? finite(r.roll) : nil,
                tiltX: plan.tiltX ? tilt(r.tiltX) : nil,
                tiltY: plan.tiltY ? tilt(r.tiltY) : nil,
                rotation: plan.rotation ? finite(r.rotation) : nil,
                phase: r.phase, est: r.estimated.intersection(plan.properties), pending: pending
            )))
            if lifts {
                down[r.key] = nil
                events.append(.contactEnded(ContactEnded(t: r.t, contact: contact, cancelled: r.phase == .cancelled)))
            } else {
                down[r.key] = OpenContact(contact: contact, t: r.t)
            }
        }
        return events
    }

    /// Events for `touchesEstimatedPropertiesUpdated`. Each correction carries the values that this update resolved.
    /// A correction that matches no pending estimate has no contact.
    public mutating func estimatesUpdated(_ readings: [PenReading], arrived: Double) -> [Event] {
        guard isUptime(arrived) else { return [] }
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
                force: resolved.contains(.force) ? force(r.force) : nil,
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
        let contacts = down.values.map(\.contact).sorted()
        down.removeAll()
        return contacts.map { .contactEnded(ContactEnded(t: t, contact: $0, cancelled: true)) }
    }

    /// An in-air reading. A non-finite height is an absent height, as a non-finite channel value is an absent channel.
    public func hover(_ r: HoverReading) -> Event? {
        guard plan.hover, isUptime(r.t), r.x.isFinite, r.y.isFinite else { return nil }
        return .hover(Hover(
            t: r.t, clock: r.clock, x: r.x, y: r.y, z: finite(r.z).map { max(0, $0) },
            altitude: plan.altitude ? finite(r.altitude) : nil,
            azimuth: plan.azimuth ? finite(r.azimuth) : nil,
            roll: plan.roll ? finite(r.roll) : nil,
            tiltX: plan.tiltX ? tilt(r.tiltX) : nil,
            tiltY: plan.tiltY ? tilt(r.tiltY) : nil,
            rotation: plan.rotation ? finite(r.rotation) : nil
        ))
    }

    public func finger(_ r: FingerReading) -> Event? {
        guard isUptime(r.t), r.x.isFinite, r.y.isFinite else { return nil }
        return .finger(Finger(t: r.t, phase: r.phase, x: r.x, y: r.y))
    }

    private func finite(_ value: Double?) -> Double? {
        value.flatMap { $0.isFinite ? $0 : nil }
    }

    private func positive(_ value: Double?) -> Double? {
        value.flatMap { $0.isFinite && $0 > 0 ? $0 : nil }
    }

    private func force(_ value: Double?) -> Double? {
        value.flatMap { $0.isFinite && $0 >= 0 ? $0 : nil }
    }

    /// A tilt component is -1 to 1. The decoder refuses a value outside that range.
    private func tilt(_ value: Double?) -> Double? {
        value.flatMap { (-1...1).contains($0) ? $0 : nil }
    }

    private func isUptime(_ t: Double) -> Bool { t.isFinite && t >= 0 }
}

// MARK: - JSON

extension ChannelPlan {
    /// A 1.0 header has the five Pencil keys only. An absent tablet key reads as false.
    init(_ f: Fields) throws(DecodeError) {
        self.init(force: try f.bool("force"), altitude: try f.bool("altitude"), azimuth: try f.bool("azimuth"),
                  roll: try f.bool("roll"), hover: try f.bool("hover"),
                  tiltX: f.has("tiltX") ? try f.bool("tiltX") : false,
                  tiltY: f.has("tiltY") ? try f.bool("tiltY") : false,
                  rotation: f.has("rotation") ? try f.bool("rotation") : false)
    }

    var json: JSON {
        .object([
            ("force", .bool(force)), ("altitude", .bool(altitude)), ("azimuth", .bool(azimuth)),
            ("roll", .bool(roll)), ("hover", .bool(hover)), ("tiltX", .bool(tiltX)), ("tiltY", .bool(tiltY)),
            ("rotation", .bool(rotation)),
        ])
    }
}
