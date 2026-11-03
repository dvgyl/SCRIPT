import ScriptCore
import SwiftUI

/// A bench session, for the operator only: the run screen between runs, and the task screen of one run. The program
/// and the run index stay chosen from one run to the next.
struct BenchScreen: View {
    let store: SessionStore
    let live: LiveSession
    @State private var program: ProgramID
    @State private var run = BenchRun.runs.lowerBound

    init(store: SessionStore, live: LiveSession) {
        self.store = store
        self.live = live
        _program = State(initialValue: store.contract.benchPrograms.listed[0].id)
    }

    var body: some View {
        if let current = store.currentRun {
            BenchTaskScreen(store: store, run: current)
                .id(current.trial.map { "run-\($0.rawValue)" } ?? "chosen")
        } else {
            BenchRunScreen(store: store, live: live, program: $program, run: $run)
        }
    }
}

/// The operator picks the program and the run index, checks the run id, and starts the run.
struct BenchRunScreen: View {
    let store: SessionStore
    let live: LiveSession
    @Binding var program: ProgramID
    @Binding var run: Int
    @State private var ending = false

    var body: some View {
        let programs = store.contract.benchPrograms.listed
        let choice = BenchRunPlan(program: program, run: run)
        let refusal = store.runRefusal(choice)
        let digest = store.digest ?? JournalDigest()
        NavigationStack {
            List {
                Section("Next run") {
                    if refusal != .otherProtocol {
                        Picker("Program", selection: $program) {
                            Section("Main programs") {
                                ForEach(programs.filter { $0.fault == nil }, id: \.id) { Text(verbatim: $0.id.rawValue).tag($0.id) }
                            }
                            Section("Fault programs") {
                                ForEach(programs.filter { $0.fault != nil }, id: \.id) { Text(verbatim: $0.id.rawValue).tag($0.id) }
                            }
                        }
                        .accessibilityIdentifier("program")
                        LabeledContent("Run") {
                            Picker("Run", selection: $run) {
                                ForEach(BenchRun.runs, id: \.self) { Text(verbatim: String($0)).tag($0) }
                            }
                            .pickerStyle(.segmented)
                            .labelsHidden()
                            .frame(maxWidth: 480)
                            .accessibilityIdentifier("runIndex")
                        }
                        HStack {
                            Text("Run ID")
                            Spacer(minLength: 16)
                            Text(verbatim: store.runID(choice)?.rawValue ?? "")
                                .monospaced()
                                .foregroundStyle(.secondary)
                                .accessibilityIdentifier("runID")
                        }
                        Button("Start the run") { store.chooseRun(choice) }
                            .disabled(refusal != nil)
                            .accessibilityIdentifier("startRun")
                    }
                    if let line = refusal?.runLine {
                        Text(line).foregroundStyle(.red).accessibilityIdentifier("runRefusal")
                    }
                }
                if !digest.trials.isEmpty {
                    Section("Runs") {
                        ForEach(Array(digest.trials.enumerated()), id: \.offset) { _, trial in TrialRow(trial: trial) }
                    }
                }
                Section {
                    Button("End the session", role: .destructive) { ending = true }
                        .accessibilityIdentifier("endBench")
                }
            }
            .navigationTitle(Text("Bench \(live.opened.benchLabel ?? "")"))
            .confirmationDialog("End the bench session?", isPresented: $ending, titleVisibility: .visible) {
                Button("End the session", role: .destructive, action: store.endBench)
                    .accessibilityIdentifier("confirmEndBench")
            } message: {
                Text("A closed session takes no more runs.")
            }
        }
    }
}

/// One run: the run id, the stimulus of the program's task at true scale from the origin of the task view, Begin and
/// Done, and the marks that the run offers.
struct BenchTaskScreen: View {
    let store: SessionStore
    let run: SessionStore.RunModel
    @State private var stopping = false

    private var drawing: StimulusDrawing? {
        guard let stimulus = run.spec.stimulus, let origin = run.origin, let mm = run.mmPerPoint else { return nil }
        return StimulusDrawing(stimulus: stimulus, origin: origin, mmPerPoint: mm)
    }

    var body: some View {
        VStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text(verbatim: run.runID.rawValue)
                        .font(.title2.weight(.semibold).monospaced())
                        .lineLimit(1)
                        .minimumScaleFactor(0.5)
                        .accessibilityIdentifier("runID")
                    Spacer(minLength: 16)
                    Button("Stop the run", role: .destructive) { stopping = true }
                        .buttonStyle(.bordered)
                        .tint(.red)
                        .disabled(run.trial == nil)
                        .accessibilityIdentifier("stopRun")
                }
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 12) { marks }
                    VStack(alignment: .leading, spacing: 8) { marks }
                }
            }
            .padding(.horizontal, 32)
            .padding(.vertical, 16)
            GeometryReader { geometry in
                CaptureSurface(store: store, drawing: drawing, accepting: run.began)
                    .onAppear { start(geometry.size) }
                    .onChange(of: geometry.size) { _, size in start(size) }
            }
            .overlay(alignment: .top) { Divider() }
            .overlay(alignment: .bottom) { Divider() }
            Group {
                if run.began {
                    Button("Done", action: store.done).accessibilityIdentifier("done")
                } else {
                    Button("Begin", action: store.begin).accessibilityIdentifier("begin").disabled(run.trial == nil)
                }
            }
            .buttonStyle(PrimaryButtonStyle())
            .padding(.horizontal, 32)
            .padding(.vertical, 20)
        }
        .background(Color(UIColor.systemBackground))
        .confirmationDialog("Stop this run?", isPresented: $stopping, titleVisibility: .visible) {
            Button("Stop the run", role: .destructive, action: store.stopRun)
        }
    }

    @ViewBuilder
    private var marks: some View {
        ForEach(run.markCodes, id: \.self) { code in
            let marked = run.marks.contains(code)
            Button {
                store.mark(code)
            } label: {
                // Two views, so the marked state shows at once instead of through a symbol transition.
                if marked {
                    Label(code.name, systemImage: "checkmark.circle.fill")
                } else {
                    Label(code.name, systemImage: "flag")
                }
            }
            .buttonStyle(.bordered)
            .disabled(run.trial == nil || marked)
            .accessibilityAddTraits(marked ? .isSelected : [])
            .accessibilityIdentifier("mark-\(code.rawValue)")
        }
    }

    private func start(_ size: CGSize) {
        guard run.trial == nil, let view = ViewSize(width: Double(size.width), height: Double(size.height)) else { return }
        store.startRun(view: view)
    }
}

extension Rejection {
    /// The line that the run screen shows for a run that the session refuses.
    var runLine: LocalizedStringKey? {
        switch self {
        case .repeatedRun: "This session already has this run ID."
        case .otherProtocol: "This session uses another bench protocol than this app, so it takes no more runs."
        default: nil
        }
    }
}

extension MarkCode {
    var name: LocalizedStringKey {
        switch self {
        case .repeatRequested: "Repeat requested"
        case .disturbance: "Disturbance"
        case .deviceProblem: "Device problem"
        case .exclude: "Exclude"
        }
    }
}
