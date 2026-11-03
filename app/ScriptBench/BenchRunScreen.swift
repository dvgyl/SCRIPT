import ScriptCore
import SwiftUI

/// The open session: the run controls beside the tablet area.
struct BenchRunScreen: View {
    let store: BenchStore
    let live: LiveSession
    @State private var program: ProgramID
    @State private var run = 1
    @State private var task: TaskID = BenchContract.qualificationTasks[0]
    @State private var closing = false

    init(store: BenchStore, live: LiveSession) {
        self.store = store
        self.live = live
        _program = State(initialValue: store.contract.programList.first?.id ?? "loops-l-v20")
    }

    private var isBench: Bool { live.opened.purpose == .bench }
    private var choice: RunChoice { isBench ? .bench(BenchRunPlan(program: program, run: run)) : .qualification(task) }
    private var openTrial: OpenTrial? {
        if case .drawing(let open) = store.state?.phase { open } else { nil }
    }

    var body: some View {
        HStack(spacing: 0) {
            controls.frame(width: 340)
            Divider()
            TabletSurface(store: store, geometry: live.geometry, stimulus: stimulus, origin: live.origin)
                .frame(minWidth: 480, minHeight: 320)
        }
        .navigationTitle(title)
    }

    private var title: Text {
        switch live.opened.subject {
        case .bench(let facts): Text("Bench \(facts.role.rawValue)-\(facts.mounting.rawValue)")
        case .participant: Text("Qualification")
        }
    }

    /// The stimulus of the open trial, or of the run that Begin starts.
    private var stimulus: Stimulus? {
        let task = openTrial?.task ?? store.plan(choice)?.task
        return task.flatMap { store.contract.battery.task($0)?.stimulus }
    }

    private var controls: some View {
        Form {
            if let openTrial {
                Section("Open run") {
                    Text(verbatim: live.labels.last ?? openTrial.task.rawValue).font(.headline).monospaced()
                    Button("Done", action: store.done)
                        .keyboardShortcut(.defaultAction)
                    Menu("Mark") {
                        ForEach(store.markCodes, id: \.self) { code in
                            Button(code.name) { store.mark(code) }
                        }
                    }
                }
            } else {
                Section("Next run") {
                    let refusal = store.refusal(choice)
                    if refusal != .otherProtocol {
                        if isBench {
                            Picker("Program", selection: $program) {
                                ForEach(store.contract.programList, id: \.id) { Text(verbatim: $0.id.rawValue).tag($0.id) }
                            }
                            Stepper("Run \(run)", value: $run, in: BenchRun.runs)
                            LabeledContent("Run id") {
                                Text(verbatim: store.runID(choice) ?? "").monospaced()
                            }
                        } else {
                            Picker("Task", selection: $task) {
                                ForEach(BenchContract.qualificationTasks, id: \.self) { Text(verbatim: $0.rawValue).tag($0) }
                            }
                        }
                        Button("Begin") { store.begin(choice) }
                            .keyboardShortcut(.defaultAction)
                            .disabled(store.plan(choice) == nil || live.stopped || refusal != nil)
                    }
                    if let line = refusal?.runLine {
                        Text(line).foregroundStyle(.red)
                    }
                }
            }
            Section("Origin") {
                LabeledContent("Origin") {
                    Text("\(Int(live.origin.x.rounded())), \(Int(live.origin.y.rounded())) lines").monospacedDigit()
                }
                Button("Set origin", action: store.setOrigin)
                    .disabled(!store.penInRange)
            }
            if let trials = store.state?.session.trials, !trials.isEmpty {
                Section("Runs") {
                    ForEach(trials.reversed(), id: \.index) { trial in
                        LabeledContent {
                            Text(trial.outcome.name)
                        } label: {
                            Text(verbatim: trial.index.rawValue < live.labels.count
                                 ? live.labels[trial.index.rawValue] : trial.task.rawValue)
                                .monospaced()
                        }
                    }
                }
            }
            Section {
                Button(live.stopped ? "Leave the session" : "Close the session") {
                    closing = true
                    Task {
                        if live.stopped { await store.leave() } else { await store.close() }
                        closing = false
                    }
                }
                .disabled(openTrial != nil || closing)
            }
        }
        .formStyle(.grouped)
    }
}

extension Rejection {
    /// The line that the run screen shows for a run that the session refuses.
    var runLine: LocalizedStringKey? {
        switch self {
        case .repeatedRun: "This session already has this run id."
        case .otherProtocol: "This session uses another bench protocol than this app, so it takes no more runs."
        default: nil
        }
    }
}

extension MarkCode {
    var name: LocalizedStringKey {
        switch self {
        case .disturbance: "Disturbance"
        case .deviceProblem: "Device problem"
        case .exclude: "Exclude"
        case .repeatRequested: "Repeat requested"
        }
    }
}

extension TrialOutcome {
    var name: LocalizedStringKey {
        switch self {
        case .completed: "Completed"
        case .timeLimit: "Time limit"
        case .declined: "Declined"
        case .unable: "Unable"
        case .aborted: "Aborted"
        case .interrupted: "Interrupted"
        case .technicalFailure: "Technical failure"
        case .open: "Open"
        }
    }
}
