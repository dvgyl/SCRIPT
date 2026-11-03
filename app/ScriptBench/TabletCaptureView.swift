import AppKit
import ScriptCore
import SwiftUI

extension TabletEvent {
    /// Copies the fields of an AppKit event. It reads each tablet field only for the event types for which AppKit
    /// documents it as valid.
    init(_ event: NSEvent) {
        switch Self.kind(type: event.type, subtype: { event.subtype }) {
        case .point(let source): self = .point(TabletPoint(event, source: source))
        case .proximity: self = .proximity(TabletProximity(event))
        case .other: self = .other
        }
    }
}

extension TabletPoint {
    /// AppKit documents `pressure` for mouse-down, mouse-up, mouse-drag, and tablet-point events only, so a
    /// mouse-moved report, which is a hover, reads none.
    init(_ event: NSEvent, source: Source) {
        self.init(source: source, t: event.timestamp, device: event.deviceID, absoluteX: event.absoluteX,
                  absoluteY: event.absoluteY, pressure: source == .moved ? 0 : Double(event.pressure),
                  tiltX: Double(event.tilt.x), tiltY: Double(event.tilt.y), rotation: Double(event.rotation))
    }
}

extension TabletProximity {
    init(_ event: NSEvent) {
        self.init(t: event.timestamp, device: event.deviceID, entering: event.isEnteringProximity,
                  pointingDevice: event.pointingDeviceType)
    }
}

/// The ink of the open trial and the pen position, for the capture view. The store fills it from the recorded
/// lines, so the view shows what the journal holds.
@MainActor
final class InkTrace {
    private(set) var strokes: [[Point]] = []
    private(set) var pen: Point?
    var onChange: (() -> Void)?

    func clear() {
        strokes = []
        onChange?()
    }

    func beginStroke() { strokes.append([]) }

    func add(_ point: Point) {
        if strokes.isEmpty { strokes.append([]) }
        strokes[strokes.count - 1].append(point)
        onChange?()
    }

    func move(_ point: Point?) {
        guard point != pen else { return }
        pen = point
        onChange?()
    }
}

/// The only AppKit view. It draws the tablet's active area, the stimulus at the origin, the ink, and the pen. The app's
/// event monitor reads the tablet events, so this view never needs the pointer above it. The view reports when its
/// window loses the keyboard focus.
final class TabletCaptureView: NSView {
    var area = ViewSize(width: 1, height: 1)!
    var linesPerMM = 1.0
    var stimulus: Stimulus?
    var origin = Point(x: 0, y: 0)
    var onFocusLost: (() -> Void)?
    private let ink: InkTrace
    private var resignObserver: (any NSObjectProtocol)?

    init(ink: InkTrace) {
        self.ink = ink
        super.init(frame: .zero)
        ink.onChange = { [weak self] in self?.needsDisplay = true }
        setAccessibilityElement(true)
        setAccessibilityRole(.image)
        setAccessibilityLabel(String(localized: "Tablet area"))
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("TabletCaptureView is created in code") }

    override var isFlipped: Bool { true }

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        if let resignObserver { NotificationCenter.default.removeObserver(resignObserver) }
        resignObserver = nil
        guard let window else { return }
        window.acceptsMouseMovedEvents = true
        resignObserver = NotificationCenter.default.addObserver(
            forName: NSWindow.didResignKeyNotification, object: window, queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated { self?.onFocusLost?() }
        }
    }

    override func draw(_ dirtyRect: NSRect) {
        NSColor.white.setFill()
        bounds.fill()
        let inset: CGFloat = 12
        let scale = min((bounds.width - 2 * inset) / area.width, (bounds.height - 2 * inset) / area.height)
        guard scale > 0 else { return }
        let left = (bounds.width - area.width * scale) / 2
        let top = (bounds.height - area.height * scale) / 2
        func point(_ p: Point) -> NSPoint { NSPoint(x: left + p.x * scale, y: top + p.y * scale) }

        NSColor(white: 0.85, alpha: 1).setStroke()
        let frame = NSBezierPath(rect: NSRect(x: left, y: top, width: area.width * scale, height: area.height * scale))
        frame.lineWidth = 1
        frame.stroke()

        if case .paths(_, let strokeMM, _, let paths) = stimulus {
            let figure = NSBezierPath()
            for line in paths {
                let points = line.map { point(Point(x: origin.x + $0.x * linesPerMM, y: origin.y + $0.y * linesPerMM)) }
                figure.move(to: points[0])
                for p in points.dropFirst() { figure.line(to: p) }
            }
            figure.lineWidth = max(1, strokeMM * linesPerMM * scale)
            figure.lineCapStyle = .round
            figure.lineJoinStyle = .round
            NSColor(white: 0.75, alpha: 1).setStroke()
            figure.stroke()
        }

        NSColor(white: 0.08, alpha: 1).setStroke()
        for stroke in ink.strokes where !stroke.isEmpty {
            let path = NSBezierPath()
            path.move(to: point(stroke[0]))
            for p in stroke.dropFirst() { path.line(to: point(p)) }
            path.lineWidth = 2
            path.lineCapStyle = .round
            path.lineJoinStyle = .round
            path.stroke()
        }

        let mark = point(origin)
        let cross = NSBezierPath()
        cross.move(to: NSPoint(x: mark.x - 8, y: mark.y))
        cross.line(to: NSPoint(x: mark.x + 8, y: mark.y))
        cross.move(to: NSPoint(x: mark.x, y: mark.y - 8))
        cross.line(to: NSPoint(x: mark.x, y: mark.y + 8))
        NSColor.systemBlue.setStroke()
        cross.lineWidth = 1.5
        cross.stroke()

        if let pen = ink.pen {
            let at = point(pen)
            NSColor.systemOrange.setFill()
            NSBezierPath(ovalIn: NSRect(x: at.x - 4, y: at.y - 4, width: 8, height: 8)).fill()
        }
    }
}

/// The capture view in SwiftUI.
struct TabletSurface: NSViewRepresentable {
    let store: BenchStore
    let geometry: TabletGeometry
    let stimulus: Stimulus?
    let origin: Point

    func makeNSView(context: Context) -> TabletCaptureView {
        let view = TabletCaptureView(ink: store.ink)
        view.onFocusLost = { [weak store] in store?.focusLost() }
        return view
    }

    func updateNSView(_ view: TabletCaptureView, context: Context) {
        view.area = geometry.activeAreaLines
        view.linesPerMM = geometry.linesPerMM
        view.stimulus = stimulus
        view.origin = origin
        view.needsDisplay = true
    }
}
