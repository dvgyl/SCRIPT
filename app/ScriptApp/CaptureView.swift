import ScriptCore
import SwiftUI
import UIKit

/// The stimulus of one trial at true millimetre scale: one multiply and one add for each point.
struct StimulusDrawing: Equatable {
    let stimulus: Stimulus
    let origin: Point
    let mmPerPoint: Double

    func point(_ p: PointMM) -> CGPoint {
        CGPoint(x: origin.x + p.x / mmPerPoint, y: origin.y + p.y / mmPerPoint)
    }

    func length(_ mm: Double) -> CGFloat { CGFloat(mm / mmPerPoint) }
}

/// The only UIKit view. It records coalesced Pencil touches, estimate updates, hover, and finger contacts, and draws
/// ink for Pencil input only. Predicted touches go to the ink preview and never to the journal. The surface is
/// white paper with dark ink in both appearances, so every participant sees the same stimulus.
final class CaptureView: UIView {
    var onPen: (([PenReading]) -> Void)?
    var onEstimates: (([PenReading], Double) -> Void)?
    var onHover: ((HoverReading) -> Void)?
    var onFinger: ((FingerReading) -> Void)?
    /// False until the participant taps Begin.
    var accepting = false

    private let directTouchAsPen: Bool
    private let stimulusLayer = CAShapeLayer()
    private let startLayer = CAShapeLayer()
    private let textLayer = CATextLayer()
    private let inkLayer = CALayer()
    private let previewLayer = CAShapeLayer()
    private var strokes: [TouchKey: (layer: CAShapeLayer, path: CGMutablePath)] = [:]
    /// The touch of each pending estimate, because UIKit can pass another touch object with the update.
    private var updateKeys: [Int: TouchKey] = [:]
    private var drawing: StimulusDrawing?

    private static let stimulusColor = UIColor(white: 0.55, alpha: 1).cgColor
    private static let textColor = UIColor(white: 0.25, alpha: 1).cgColor
    private static let inkColor = UIColor(white: 0.08, alpha: 1).cgColor

    init(directTouchAsPen: Bool) {
        self.directTouchAsPen = directTouchAsPen
        super.init(frame: .zero)
        overrideUserInterfaceStyle = .light
        backgroundColor = .white
        isMultipleTouchEnabled = true
        for shape in [stimulusLayer, previewLayer] {
            shape.fillColor = nil
            shape.lineCap = .round
            shape.lineJoin = .round
        }
        stimulusLayer.strokeColor = Self.stimulusColor
        startLayer.fillColor = Self.stimulusColor
        previewLayer.strokeColor = Self.inkColor
        previewLayer.lineWidth = 2.5
        textLayer.foregroundColor = Self.textColor
        textLayer.isWrapped = false
        for sublayer in [stimulusLayer, startLayer, textLayer, inkLayer, previewLayer] { layer.addSublayer(sublayer) }
        addGestureRecognizer(UIHoverGestureRecognizer(target: self, action: #selector(hovered(_:))))
        isAccessibilityElement = true
        accessibilityLabel = String(localized: "Drawing area")
        accessibilityTraits = .allowsDirectInteraction
        accessibilityIdentifier = "drawingArea"
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("CaptureView is created in code") }

    override func layoutSubviews() {
        super.layoutSubviews()
        for sublayer in [stimulusLayer, startLayer, inkLayer, previewLayer] { sublayer.frame = bounds }
    }

    // MARK: Stimulus

    func show(_ drawing: StimulusDrawing?) {
        guard drawing != self.drawing else { return }
        self.drawing = drawing
        stimulusLayer.path = nil
        startLayer.path = nil
        textLayer.string = nil
        guard let drawing else { return }
        switch drawing.stimulus {
        case .paths(_, let strokeMM, let start, let paths):
            let path = CGMutablePath()
            for line in paths {
                path.addLines(between: line.map(drawing.point))
            }
            stimulusLayer.path = path
            stimulusLayer.lineWidth = drawing.length(strokeMM)
            if let start {
                let radius = drawing.length(1.5)
                let center = drawing.point(start)
                startLayer.path = CGPath(ellipseIn: CGRect(x: center.x - radius, y: center.y - radius,
                                                           width: 2 * radius, height: 2 * radius), transform: nil)
            }
        case .text(let frame, let text, let fontSizeMM):
            let font = UIFont.systemFont(ofSize: drawing.length(fontSizeMM))
            textLayer.font = font
            textLayer.fontSize = font.pointSize
            textLayer.string = text
            textLayer.contentsScale = traitCollection.displayScale
            let width = max(drawing.length(frame.x), (text as NSString).size(withAttributes: [.font: font]).width)
            textLayer.frame = CGRect(origin: CGPoint(x: drawing.origin.x, y: drawing.origin.y),
                                     size: CGSize(width: width, height: max(drawing.length(frame.y), font.lineHeight)))
        }
    }

    // MARK: Touches

    private func isPen(_ touch: UITouch) -> Bool {
        touch.type == .pencil || (directTouchAsPen && touch.type == .direct)
    }

    override func touchesBegan(_ touches: Set<UITouch>, with event: UIEvent?) { handle(touches, event) }
    override func touchesMoved(_ touches: Set<UITouch>, with event: UIEvent?) { handle(touches, event) }
    override func touchesEnded(_ touches: Set<UITouch>, with event: UIEvent?) { handle(touches, event) }
    override func touchesCancelled(_ touches: Set<UITouch>, with event: UIEvent?) { handle(touches, event) }

    private func handle(_ touches: Set<UITouch>, _ event: UIEvent?) {
        guard accepting else { return }
        for touch in touches {
            if isPen(touch) {
                let key = TouchKey(touch)
                let measured = (event?.coalescedTouches(for: touch) ?? [touch]).map { PenReading($0, in: self, key: key) }
                for reading in measured where !reading.expectingUpdates.isEmpty {
                    if let update = reading.update { updateKeys[update.rawValue] = key }
                }
                onPen?(measured)
                ink(measured, key: key)
                preview(touch.phase == .moved ? (event?.predictedTouches(for: touch) ?? []) : [], key: key)
            } else if touch.type == .direct, let phase = FingerPhase(touch.phase) {
                let p = touch.location(in: self)
                onFinger?(FingerReading(t: touch.timestamp, phase: phase, x: Double(p.x), y: Double(p.y)))
            }
        }
    }

    override func touchesEstimatedPropertiesUpdated(_ touches: Set<UITouch>) {
        let readings = touches.compactMap { touch -> PenReading? in
            guard let index = touch.estimationUpdateIndex?.intValue else { return nil }
            let reading = PenReading(touch, in: self, key: updateKeys[index] ?? TouchKey(touch))
            if touch.estimatedPropertiesExpectingUpdates.isEmpty { updateKeys[index] = nil }
            return reading
        }
        guard !readings.isEmpty else { return }
        onEstimates?(readings, ProcessInfo.processInfo.systemUptime)
    }

    @objc private func hovered(_ recognizer: UIHoverGestureRecognizer) {
        guard accepting, recognizer.state == .began || recognizer.state == .changed else { return }
        let p = recognizer.location(in: self)
        onHover?(HoverReading(t: ProcessInfo.processInfo.systemUptime, x: Double(p.x), y: Double(p.y),
                              z: Double(recognizer.zOffset), altitude: Double(recognizer.altitudeAngle),
                              azimuth: Double(recognizer.azimuthAngle(in: self)), roll: Double(recognizer.rollAngle)))
    }

    // MARK: Ink

    private func ink(_ readings: [PenReading], key: TouchKey) {
        for reading in readings {
            let point = CGPoint(x: reading.x, y: reading.y)
            if reading.phase == .began || strokes[key] == nil {
                let shape = CAShapeLayer()
                shape.frame = bounds
                shape.strokeColor = Self.inkColor
                shape.fillColor = nil
                shape.lineWidth = 2.5
                shape.lineCap = .round
                shape.lineJoin = .round
                inkLayer.addSublayer(shape)
                let path = CGMutablePath()
                path.move(to: point)
                path.addLine(to: point)
                strokes[key] = (shape, path)
            } else {
                strokes[key]?.path.addLine(to: point)
            }
        }
        if let stroke = strokes[key] { stroke.layer.path = stroke.path.copy() }
        if readings.last?.phase == .ended || readings.last?.phase == .cancelled { strokes[key] = nil }
    }

    private func preview(_ predicted: [UITouch], key: TouchKey) {
        guard let last = strokes[key]?.path.currentPoint, !predicted.isEmpty else {
            previewLayer.path = nil
            return
        }
        let points = predicted.map { PreviewPoint($0.preciseLocation(in: self)) }.map { CGPoint(x: $0.x, y: $0.y) }
        let path = CGMutablePath()
        path.addLines(between: [last] + points)
        previewLayer.path = path
    }
}

extension TouchKey {
    init(_ touch: UITouch) {
        self.init(UInt(bitPattern: Unmanaged.passUnretained(touch).toOpaque()))
    }
}

extension PreviewPoint {
    init(_ p: CGPoint) { self.init(x: Double(p.x), y: Double(p.y)) }
}

extension PenReading {
    @MainActor init(_ touch: UITouch, in view: UIView, key: TouchKey) {
        let p = touch.preciseLocation(in: view)
        self.init(
            key: key, t: touch.timestamp, x: Double(p.x), y: Double(p.y),
            force: Double(touch.force), maxForce: Double(touch.maximumPossibleForce),
            altitude: Double(touch.altitudeAngle), azimuth: Double(touch.azimuthAngle(in: view)),
            roll: Double(touch.rollAngle), phase: PenPhase(touch.phase),
            estimated: TouchProperties(rawValue: Int(touch.estimatedProperties.rawValue)),
            expectingUpdates: TouchProperties(rawValue: Int(touch.estimatedPropertiesExpectingUpdates.rawValue)),
            update: touch.estimationUpdateIndex.map { UpdateIndex($0.intValue) }
        )
    }
}

extension PenPhase {
    init(_ phase: UITouch.Phase) {
        switch phase {
        case .began: self = .began
        case .stationary: self = .stationary
        case .ended: self = .ended
        case .cancelled: self = .cancelled
        default: self = .moved
        }
    }
}

extension FingerPhase {
    /// A finger line marks only where a contact begins and ends.
    init?(_ phase: UITouch.Phase) {
        switch phase {
        case .began: self = .began
        case .ended: self = .ended
        case .cancelled: self = .cancelled
        default: return nil
        }
    }
}

/// The capture view in SwiftUI. The store receives every reading; the view draws the stimulus and the ink.
struct CaptureSurface: UIViewRepresentable {
    let store: SessionStore
    let drawing: StimulusDrawing?
    let accepting: Bool

    func makeUIView(context: Context) -> CaptureView {
        let view = CaptureView(directTouchAsPen: store.device.input == .directTouchAsPen)
        view.onPen = { [weak store] in store?.pen($0) }
        view.onEstimates = { [weak store] in store?.estimatesUpdated($0, arrived: $1) }
        view.onHover = { [weak store] in store?.hover($0) }
        view.onFinger = { [weak store] in store?.finger($0) }
        return view
    }

    func updateUIView(_ view: CaptureView, context: Context) {
        view.accepting = accepting
        view.show(drawing)
    }
}
