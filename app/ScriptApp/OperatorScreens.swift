import ScriptCore
import SwiftUI
import UIKit

/// The operator took over in the middle of the session.
struct PausedScreen: View {
    let store: SessionStore
    let live: LiveSession

    var body: some View {
        NavigationStack {
            List {
                Section {
                    LabeledContent("Participant", value: live.opened.participant.rawValue)
                    LabeledContent("Administration") { Text(live.opened.administration.name) }
                }
                Section {
                    Button("Return to the participant", action: store.returnToParticipant)
                        .accessibilityIdentifier("returnToParticipant")
                    Button("End the session", role: .destructive, action: store.endEarly)
                        .accessibilityIdentifier("endSession")
                    Button("Leave the session open and go to Home", action: store.leave)
                }
            }
            .navigationTitle("Session paused")
        }
        .demoBanner(live.configuration.demo)
    }
}

/// The operator review after the hand-back. Each safety flag shows as a neutral alert that needs one action code.
/// The review never shows a response or a score.
struct ReviewScreen: View {
    let store: SessionStore
    let live: LiveSession
    @State private var codes: [Int: Code] = [:]

    var body: some View {
        let digest = store.digest ?? JournalDigest()
        let session = store.state?.session
        let open = session?.safety.unactionedFlags ?? 0
        NavigationStack {
            List {
                if !digest.flags.isEmpty {
                    Section("Safety follow-up") {
                        ForEach(Array(digest.flags.enumerated()), id: \.offset) { n, flag in
                            flagRow(n, flag, actioned: n < digest.actions)
                        }
                    }
                }
                Section("Trials") {
                    ForEach(Array(digest.trials.enumerated()), id: \.offset) { _, trial in TrialRow(trial: trial) }
                }
                Section {
                    if session?.blocks.isEmpty == false {
                        Button("Show the support list to the participant", action: store.showSupportList)
                            .disabled(open > 0)
                            .accessibilityIdentifier("showSupportList")
                    } else {
                        Button("Close the session", action: store.closeFromReview)
                            .accessibilityIdentifier("closeSession")
                    }
                } footer: {
                    if open > 0 { Text("Record an action for each safety item first.") }
                }
            }
            .navigationTitle("Session review")
        }
        .demoBanner(live.configuration.demo)
    }

    @ViewBuilder
    private func flagRow(_ n: Int, _ flag: JournalDigest.Flag, actioned: Bool) -> some View {
        let instrument = live.rules.instruments.instrument(flag.instrument)
        let number = (instrument?.items.firstIndex { $0.id == flag.item } ?? 0) + 1
        VStack(alignment: .leading, spacing: 12) {
            Label {
                Text("A safety item needs follow-up under your protocol: \(instrument?.title ?? flag.instrument.rawValue), item \(number)")
            } icon: {
                Image(systemName: "exclamationmark.circle").foregroundStyle(Palette.accent)
            }
            .accessibilityIdentifier("safetyAlert")
            if actioned {
                Label("Action recorded", systemImage: "checkmark.circle")
                    .foregroundStyle(.secondary)
                    .accessibilityIdentifier("actionRecorded")
            } else {
                Picker("Action", selection: Binding(get: { codes[n] }, set: { codes[n] = $0 })) {
                    Text("Choose an action").tag(Code?.none)
                    ForEach(live.rules.actionCodes, id: \.self) { code in
                        Text(code.words).tag(Code?.some(code))
                    }
                }
                .accessibilityIdentifier("actionPicker")
                Button("Record action") {
                    if let code = codes[n] { store.recordAction(code) }
                }
                .buttonStyle(.borderless)
                // Each action answers the oldest open flag, so only the first open flag takes an action.
                .disabled(codes[n] == nil || n != store.digest?.actions)
                .accessibilityIdentifier("recordAction")
            }
        }
        .padding(.vertical, 6)
    }
}

struct TrialRow: View {
    let trial: JournalDigest.Trial

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(verbatim: trial.task.rawValue).font(.body.weight(.medium))
                if trial.attempt > 1 { Text("attempt \(trial.attempt)").foregroundStyle(.secondary) }
                Spacer()
                Text(outcome).foregroundStyle(.secondary)
            }
            // One line when it fits, else one fact under the other, as at the largest text sizes.
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 16) { facts }
                VStack(alignment: .leading, spacing: 2) { facts }
            }
            .font(.subheadline)
            .foregroundStyle(.secondary)
        }
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder
    private var facts: some View {
        if let began = trial.began, let ended = trial.ended {
            Text("\(ended - began, format: .number.precision(.fractionLength(1))) s").fixedSize()
        }
        Text("^[\(trial.samples) sample](inflect: true)").fixedSize()
        if let timing = SampleTiming(trial.intervals) {
            Text("\(1 / timing.median, format: .number.precision(.significantDigits(1...3))) Hz").fixedSize()
        }
        Text(channels).fixedSize()
    }

    private var outcome: LocalizedStringKey {
        switch trial.reason {
        case nil: "Open"
        case .participantDone: "Done"
        case .timeLimit: "Time limit"
        case .declined: "Declined"
        case .unable: "Unable"
        case .operatorAbort: "Stopped by the operator"
        case .backgrounded: "Interrupted"
        case .technicalFailure: "Technical failure"
        }
    }

    private var channels: String {
        var names: [String] = []
        if trial.force { names.append(String(localized: "force")) }
        if trial.tilt { names.append(String(localized: "tilt")) }
        if trial.roll { names.append(String(localized: "roll")) }
        if trial.hover > 0 { names.append(String(localized: "hover")) }
        return names.isEmpty ? String(localized: "position only") : names.formatted(.list(type: .and))
    }
}

/// The closed qualification session: the sample timing from the recorded samples.
struct QualificationResultScreen: View {
    let store: SessionStore
    let live: LiveSession
    @State private var result: ExportResult?
    @State private var exporting = false

    var body: some View {
        let digest = store.digest ?? JournalDigest()
        let timing = SampleTiming(digest.trials.flatMap(\.intervals))
        NavigationStack {
            List {
                Section("Recorded samples") {
                    if let timing {
                        LabeledContent("Median sample interval") {
                            Text("\(timing.median * 1000, format: .number.precision(.fractionLength(2))) ms")
                        }
                        .accessibilityIdentifier("medianInterval")
                        LabeledContent("Sample rate") {
                            Text("\(1 / timing.median, format: .number.precision(.significantDigits(1...3))) Hz")
                        }
                        LabeledContent("Intervals longer than 30 ms") {
                            Text(timing.gapShare, format: .percent.precision(.fractionLength(1)))
                        }
                        .accessibilityIdentifier("gapShare")
                        LabeledContent("Intervals", value: timing.intervals, format: .number)
                    } else {
                        Text("No contact has two samples.")
                    }
                }
                Section("Trials") {
                    ForEach(Array(digest.trials.enumerated()), id: \.offset) { _, trial in TrialRow(trial: trial) }
                }
                Section {
                    Button {
                        exporting = true
                        Task {
                            result = await store.export(live.opened.sessionID)
                            exporting = false
                        }
                    } label: {
                        if exporting { ProgressView() } else { Text("Export") }
                    }
                    .disabled(exporting)
                    .accessibilityIdentifier("qualificationExport")
                    Button("Go to Home", action: store.leave)
                }
            }
            .navigationTitle("Qualification")
            .sheet(item: $result) { result in ExportScreen(result: result) }
        }
        .demoBanner(live.configuration.demo)
    }
}

/// A write failed: the session stopped, and what reached storage is kept.
struct StoppedScreen: View {
    let store: SessionStore
    let live: LiveSession

    var body: some View {
        NavigationStack {
            ContentUnavailableView {
                Label("The session stopped", systemImage: "externaldrive.badge.exclamationmark")
            } description: {
                Text("The iPad could not save the recording. Every line saved before the failure is kept, and Home lists the session as interrupted.")
            } actions: {
                Button("Go to Home", action: store.leave)
                    .buttonStyle(.borderedProminent)
                    .accessibilityIdentifier("goHome")
            }
        }
        .demoBanner(live.configuration.demo)
    }
}

/// The export: its name, whether it is encrypted, and the Files export.
struct ExportScreen: View {
    let result: ExportResult
    @Environment(\.dismiss) private var dismiss
    @State private var saving = false

    var body: some View {
        NavigationStack {
            List {
                Section("Export") {
                    Text(verbatim: result.name)
                        .monospaced()
                        .lineLimit(1)
                        .minimumScaleFactor(0.4)
                        .accessibilityIdentifier("exportName")
                }
                Section("Encryption") {
                    Text(result.encrypted ? "Encrypted to the study key" : "Not encrypted (demo configuration)")
                        .accessibilityIdentifier("exportEncryption")
                }
                Section("Location") {
                    Text("On My iPad › SCRIPT Capture › Exports")
                }
                Section {
                    Button("Save a copy to another place in Files") { saving = true }
                }
            }
            .navigationTitle("Exported")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }.accessibilityIdentifier("exportDone")
                }
            }
            .sheet(isPresented: $saving) { FilesExporter(url: result.url) }
        }
    }
}

/// The system Files sheet that saves a copy of the export folder.
struct FilesExporter: UIViewControllerRepresentable {
    let url: URL

    func makeUIViewController(context: Context) -> UIDocumentPickerViewController {
        UIDocumentPickerViewController(forExporting: [url], asCopy: true)
    }

    func updateUIViewController(_ controller: UIDocumentPickerViewController, context: Context) {}
}
