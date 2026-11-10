import ScriptCore
import SwiftUI
import UIKit

enum Palette {
    /// A calm blue. Text in this color, and the background color on a fill of it, pass 4.5:1 in both appearances.
    static let accent = Color(UIColor { traits in
        traits.userInterfaceStyle == .dark
            ? UIColor(red: 0.47, green: 0.74, blue: 0.88, alpha: 1)
            : UIColor(red: 0.10, green: 0.35, blue: 0.52, alpha: 1)
    })
    static let onAccent = Color(UIColor.systemBackground)
    static let selection = Color(UIColor { traits in
        traits.userInterfaceStyle == .dark
            ? UIColor(red: 0.47, green: 0.74, blue: 0.88, alpha: 0.22)
            : UIColor(red: 0.10, green: 0.35, blue: 0.52, alpha: 0.10)
    })
}

/// The main action of a participant screen: wide, tall, and high in contrast.
struct PrimaryButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.title3.weight(.semibold))
            .multilineTextAlignment(.center)
            .frame(maxWidth: 520, minHeight: 64)
            .padding(.horizontal, 24)
            .foregroundStyle(isEnabled ? Palette.onAccent : Color.secondary)
            .background(isEnabled ? Palette.accent : Color(UIColor.tertiarySystemFill),
                        in: RoundedRectangle(cornerRadius: 16, style: .continuous))
            .opacity(configuration.isPressed ? 0.8 : 1)
    }
}

/// "Demo, not for research", on every operator screen of a demo configuration.
struct DemoBanner: View {
    var body: some View {
        Label("Demo, not for research", systemImage: "exclamationmark.triangle.fill")
            .font(.subheadline.weight(.semibold))
            .symbolRenderingMode(.multicolor)
            .frame(maxWidth: .infinity, minHeight: 36)
            .padding(.horizontal)
            .background(Color(UIColor.systemOrange).opacity(0.22))
            .accessibilityIdentifier("demoBanner")
    }
}

extension View {
    /// Puts the demo banner above an operator screen when the configuration is a demo. The banner takes its own
    /// space, so it never covers a navigation title.
    func demoBanner(_ demo: Bool) -> some View {
        VStack(spacing: 0) {
            if demo { DemoBanner() }
            self
        }
    }

    /// Ends the open trial with `timeLimit` at its deadline, unless the trial ended before.
    func timeLimit(of trial: TrialIndex?, at deadline: Double?, store: SessionStore) -> some View {
        task(id: trial) {
            guard let deadline, let trial else { return }
            let remaining = deadline - Clock.uptime
            if remaining > 0 { try? await Task.sleep(for: .seconds(remaining)) }
            guard !Task.isCancelled, store.openTrial == trial else { return }
            store.timeLimitReached()
        }
    }
}

/// The visible operator control on participant screens. It opens after a press-and-hold of 2 seconds and then the
/// PIN, when the configuration has one. VoiceOver users double-tap and hold. `onUnlock` learns whether the PIN was
/// entered.
struct OperatorControl: View {
    let pin: OperatorPin?
    let onUnlock: (_ withPIN: Bool) -> Void
    @State private var pressing = false
    @State private var askingPIN = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        Text("Operator")
            .font(.footnote.weight(.medium))
            .foregroundStyle(.secondary)
            .padding(.horizontal, 16)
            .frame(minWidth: 88, minHeight: 44)
            .background {
                GeometryReader { geometry in
                    Capsule()
                        .fill(Color(UIColor.tertiarySystemFill))
                        .frame(width: pressing ? geometry.size.width : 0)
                        .animation(reduceMotion ? nil : .linear(duration: pressing ? 2 : 0.15), value: pressing)
                }
                .clipShape(Capsule())
            }
            .overlay(Capsule().strokeBorder(Color(UIColor.separator)))
            .contentShape(Capsule())
            .onLongPressGesture(minimumDuration: 2, maximumDistance: 40) {
                if pin == nil { onUnlock(false) } else { askingPIN = true }
            } onPressingChanged: { pressing = $0 }
            .accessibilityElement()
            .accessibilityLabel(Text("Operator"))
            .accessibilityHint(Text("Double-tap and hold for 2 seconds."))
            .accessibilityAddTraits(.isButton)
            // Switch Control and Voice Control cannot press and hold, so the same step is also a named action.
            .accessibilityAction(named: Text("Open the operator screens")) {
                if pin == nil { onUnlock(false) } else { askingPIN = true }
            }
            .accessibilityIdentifier("operatorControl")
            .sheet(isPresented: $askingPIN) {
                if let pin { PINSheet(pin: pin) { onUnlock(true) } }
            }
    }
}

/// The operator PIN. The check is PBKDF2 with at least 600,000 iterations, so it runs off the main thread. After 5
/// wrong PINs, the store gives a delay before the next try, and the sheet takes no PIN until the delay has passed.
struct PINSheet: View {
    let pin: OperatorPin
    let onUnlock: () -> Void
    @Environment(SessionStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    @State private var entry = ""
    @State private var checking = false
    @State private var wrong = false
    /// The seconds until the next try.
    @State private var wait: TimeInterval = 0
    @FocusState private var focused: Bool

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    SecureField("PIN", text: $entry)
                        .keyboardType(.numberPad)
                        .textContentType(.oneTimeCode)
                        .focused($focused)
                        .onSubmit(check)
                        .disabled(wait > 0)
                        .accessibilityIdentifier("pinField")
                } footer: {
                    if wait > 0 {
                        Text(SessionStore.pinWaitMessage(wait)).foregroundStyle(.red).accessibilityIdentifier("pinWait")
                    } else if wrong {
                        Text("The PIN is not correct.").foregroundStyle(.red)
                    }
                }
            }
            .navigationTitle("Operator PIN")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    if checking {
                        ProgressView()
                    } else {
                        Button("Unlock", action: check).disabled(entry.isEmpty || wait > 0)
                    }
                }
            }
            .onAppear {
                focused = true
                wait = store.pinWait()
            }
            .task(id: wait > 0) {
                while wait > 0, !Task.isCancelled {
                    try? await Task.sleep(for: .seconds(1))
                    wait = store.pinWait()
                }
            }
        }
        .presentationDetents([.medium])
    }

    private func check() {
        guard !entry.isEmpty, !checking, wait == 0 else { return }
        checking = true
        let candidate = entry
        Task {
            let result = await store.checkPIN(pin, candidate)
            checking = false
            switch result {
            case .correct:
                dismiss()
                onUnlock()
            case .wrong(let delay):
                wrong = true
                entry = ""
                wait = delay
            case .locked(let delay):
                entry = ""
                wait = delay
            }
        }
    }
}

/// The frame of a participant screen: a neutral step text, the operator control, the content, and the actions.
struct ParticipantFrame<Content: View, Actions: View>: View {
    let step: Text?
    let pin: OperatorPin?
    let onOperator: (_ withPIN: Bool) -> Void
    @ViewBuilder let content: Content
    @ViewBuilder let actions: Actions

    var body: some View {
        VStack(spacing: 0) {
            HStack(alignment: .center) {
                if let step {
                    step.font(.headline).foregroundStyle(.secondary).accessibilityIdentifier("stepIndicator")
                }
                Spacer(minLength: 16)
                OperatorControl(pin: pin, onUnlock: onOperator)
            }
            .padding(.horizontal, 32)
            .padding(.top, 16)
            ScrollView {
                content
                    .frame(maxWidth: 680, alignment: .leading)
                    .padding(.horizontal, 32)
                    .padding(.vertical, 24)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .scrollBounceBehavior(.basedOnSize)
            VStack(spacing: 12) { actions }
                .padding(.horizontal, 32)
                .padding(.vertical, 24)
                .frame(maxWidth: .infinity)
        }
        .background(Color(UIColor.systemBackground))
    }
}

extension Code {
    /// An action code from the configuration as words: `clinicianAssessment` reads "Clinician assessment".
    var words: String {
        var out = ""
        for character in rawValue {
            if character.isUppercase, !out.isEmpty { out += " " + character.lowercased() } else { out.append(character) }
        }
        return out.prefix(1).uppercased() + out.dropFirst()
    }
}

extension PencilModel {
    var name: LocalizedStringKey {
        switch self {
        case .pro: "Apple Pencil Pro"
        case .second: "Apple Pencil (2nd generation)"
        case .usbC: "Apple Pencil (USB-C)"
        case .none: "None (simulator input)"
        }
    }
}

extension Purpose {
    var name: LocalizedStringKey {
        switch self {
        case .study: "Study"
        case .qualification: "Qualification"
        case .bench: "Bench"
        }
    }
}

extension Administration {
    var name: LocalizedStringKey {
        switch self {
        case .supervised: "Supervised"
        case .selfAdministered: "Self-administered"
        }
    }
}
