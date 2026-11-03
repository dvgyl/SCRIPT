import ScriptCore
import SwiftUI

/// The participant flow: what the participant, or a self-administered participant alone, sees. No screen shows a
/// score, a replay, or an interpretation, and a flagged answer changes nothing in a supervised session.
struct SessionView: View {
    let store: SessionStore
    let live: LiveSession

    var body: some View {
        switch live.stage {
        case .confirmCode: ConfirmCodeScreen(store: store, live: live)
        case .ready: ReadyScreen(store: store, live: live)
        case .hand: HandScreen(store: store, live: live)
        case .disclosure: DisclosureScreen(store: store, live: live)
        case .working: WorkingScreen(store: store, live: live)
        case .flagCrisisList, .closingCrisisList: CrisisListScreen(store: store, live: live)
        case .handBack: MessageScreen(store: store, live: live, message: "Please give the iPad back to the researcher.")
        case .finished: ThankYouScreen(store: store, live: live)
        case .paused: PausedScreen(store: store, live: live)
        case .review: ReviewScreen(store: store, live: live)
        case .qualified: QualificationResultScreen(store: store, live: live)
        case .stopped: StoppedScreen(store: store, live: live)
        }
    }
}

private extension LiveSession {
    var pin: OperatorPin? { configuration.operatorPin }
}

extension StudyConfiguration.RemoteReview {
    /// The routine review interval in whole working days, rounded up, so that half a day reads "1 working day".
    var workingDays: Int { Int(routineReviewWithinWeekdays.rounded(.up)) }

    /// The monitoring disclosure that the participant reads before the first block.
    var disclosure: AttributedString {
        AttributedString(localized: "A \(reviewerRole) from the study team reads the answers to some questions. This happens within ^[\(workingDays) working day](inflect: true).")
    }
}

/// A resumed self-administered session continues only for the participant who types its code.
struct ConfirmCodeScreen: View {
    let store: SessionStore
    let live: LiveSession
    @State private var code = ""
    @State private var wrong = false

    var body: some View {
        ParticipantFrame(step: nil, pin: live.pin, onOperator: store.operatorTookOver) {
            VStack(alignment: .leading, spacing: 24) {
                Text("Type your participant code to continue.")
                    .font(.largeTitle.weight(.semibold))
                    .fixedSize(horizontal: false, vertical: true)
                TextField("Participant code", text: $code)
                    .font(.title2)
                    .textInputAutocapitalization(.characters)
                    .autocorrectionDisabled()
                    .textFieldStyle(.roundedBorder)
                    .onSubmit(confirm)
                    .onChange(of: code) { if !code.isEmpty { wrong = false } }
                    .accessibilityIdentifier("confirmCode")
                if wrong {
                    Text("This code is not the code of this session.")
                        .font(.title3)
                        .foregroundStyle(.red)
                        .accessibilityIdentifier("codeWrong")
                }
            }
            .padding(.top, 120)
        } actions: {
            Button("Continue", action: confirm)
                .buttonStyle(PrimaryButtonStyle())
                .disabled(code.isEmpty)
                .accessibilityIdentifier("continue")
        }
    }

    /// A wrong code clears the field, and the message stays until the participant types again.
    private func confirm() {
        wrong = !store.confirmCode(code)
        if wrong { code = "" }
    }
}

struct ReadyScreen: View {
    let store: SessionStore
    let live: LiveSession

    var body: some View {
        ParticipantFrame(step: nil, pin: live.pin, onOperator: store.operatorTookOver) {
            Text(live.opened.administration == .supervised
                 ? "Please give the iPad to the participant."
                 : "Tap Start when you are ready.")
                .font(.largeTitle.weight(.semibold))
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 120)
        } actions: {
            Button("Start", action: store.start)
                .buttonStyle(PrimaryButtonStyle())
                .accessibilityIdentifier("start")
        }
    }
}

struct HandScreen: View {
    let store: SessionStore
    let live: LiveSession

    var body: some View {
        ParticipantFrame(step: nil, pin: live.pin, onOperator: store.operatorTookOver) {
            Text("Which hand do you write with?")
                .font(.largeTitle.weight(.semibold))
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 120)
        } actions: {
            Button("Left hand") { store.chooseHand(.left) }.buttonStyle(PrimaryButtonStyle())
            Button("Right hand") { store.chooseHand(.right) }.buttonStyle(PrimaryButtonStyle())
        }
    }
}

struct DisclosureScreen: View {
    let store: SessionStore
    let live: LiveSession

    var body: some View {
        ParticipantFrame(step: nil, pin: live.pin, onOperator: store.operatorTookOver) {
            VStack(alignment: .leading, spacing: 24) {
                Text("Before the questions")
                    .font(.largeTitle.weight(.semibold))
                if let review = live.configuration.safety.remoteReview {
                    Text(review.disclosure).font(.title2)
                }
                Text("If you want to talk to someone sooner, the services on the support list can help. The list comes at the end.")
                    .font(.title2)
            }
            .fixedSize(horizontal: false, vertical: true)
            .padding(.top, 60)
        } actions: {
            Button("Continue", action: store.acknowledgeDisclosure)
                .buttonStyle(PrimaryButtonStyle())
                .accessibilityIdentifier("continue")
        }
    }
}

/// A trial or a questionnaire item, from the plan and the fold.
struct WorkingScreen: View {
    let store: SessionStore
    let live: LiveSession

    var body: some View {
        if let task = store.currentTask {
            TaskScreen(store: store, live: live, task: task)
                .id(task.trial.map { "trial-\($0.rawValue)" } ?? "next-\(store.state?.session.trials.count ?? 0)")
        } else if let item = store.currentItem {
            ItemScreen(store: store, live: live, item: item)
                .id("\(item.block.rawValue)-\(item.number)")
        } else {
            ProgressView().task { store.advance() }
        }
    }
}

struct TaskScreen: View {
    let store: SessionStore
    let live: LiveSession
    let task: SessionStore.TaskModel
    /// The instruction box grows with the text size. The cap leaves room for the largest stimulus, 104 mm.
    @ScaledMetric(relativeTo: .title) private var instructionHeight = 170.0

    private var step: Text {
        if task.step.number == nil { return Text("Practice") }
        let number = task.step.number ?? 0
        return task.purpose == .qualification
            ? Text("Step \(number) of \(task.step.count)")
            : Text("Task \(number) of \(task.step.count)")
    }

    private var drawing: StimulusDrawing? {
        guard let stimulus = task.spec.stimulus, let origin = task.origin, let mm = task.mmPerPoint else { return nil }
        return StimulusDrawing(stimulus: stimulus, origin: origin, mmPerPoint: mm)
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                step.font(.headline).foregroundStyle(.secondary).accessibilityIdentifier("stepIndicator")
                Spacer(minLength: 16)
                OperatorControl(pin: live.pin, onUnlock: store.operatorTookOver)
            }
            .padding(.horizontal, 32)
            .padding(.top, 16)
            // One height for every task keeps the capture area the same for every task at one text size.
            ScrollView { instruction }
                .scrollBounceBehavior(.basedOnSize)
                .frame(height: min(instructionHeight, 440))
            GeometryReader { geometry in
                CaptureSurface(store: store, drawing: drawing, accepting: task.began)
                    .onAppear { start(geometry.size) }
                    .onChange(of: geometry.size) { _, size in start(size) }
            }
            .overlay(alignment: .top) { Divider() }
            .overlay(alignment: .bottom) { Divider() }
            Group {
                if task.began {
                    Button("Done", action: store.done).accessibilityIdentifier("done")
                } else {
                    Button("Begin", action: store.begin).accessibilityIdentifier("begin").disabled(task.trial == nil)
                }
            }
            .buttonStyle(PrimaryButtonStyle())
            .padding(.horizontal, 32)
            .padding(.vertical, 20)
        }
        .background(Color(UIColor.systemBackground))
        .task(id: task.trial) {
            guard let deadline = task.deadline, let trial = task.trial else { return }
            let remaining = deadline - Clock.uptime
            if remaining > 0 { try? await Task.sleep(for: .seconds(remaining)) }
            guard !Task.isCancelled, store.currentTask?.trial == trial else { return }
            store.timeLimitReached()
        }
    }

    private var instruction: some View {
        Text(task.instruction)
            .font(.title.weight(.medium))
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: 760, alignment: .leading)
            .padding(.horizontal, 32)
            .padding(.vertical, 20)
            .frame(maxWidth: .infinity, alignment: .leading)
            .accessibilityIdentifier("instruction")
    }

    private func start(_ size: CGSize) {
        guard task.trial == nil, let view = ViewSize(width: Double(size.width), height: Double(size.height)) else { return }
        store.startTrial(view: view)
    }
}

struct ItemScreen: View {
    let store: SessionStore
    let live: LiveSession
    let item: SessionStore.ItemModel
    @State private var choice: Choice?

    enum Choice: Hashable {
        case value(Int)
        case skip
    }

    var body: some View {
        ParticipantFrame(step: Text("Question \(item.number) of \(item.count)"), pin: live.pin,
                         onOperator: store.operatorTookOver) {
            VStack(alignment: .leading, spacing: 28) {
                if item.item.scored {
                    Text(item.instrument.stem)
                        .font(.title3)
                        .foregroundStyle(.secondary)
                }
                Text(item.item.text)
                    .font(.title.weight(.semibold))
                    .accessibilityAddTraits(.isHeader)
                    .accessibilityIdentifier("itemText")
                VStack(spacing: 12) {
                    ForEach(item.item.options, id: \.value) { option in
                        OptionRow(label: Text(option.label), selected: choice == .value(option.value)) {
                            choice = .value(option.value)
                        }
                        .accessibilityIdentifier("option-\(option.value)")
                    }
                }
                OptionRow(label: Text("Prefer not to answer"), selected: choice == .skip) { choice = .skip }
                    .accessibilityIdentifier("option-skip")
            }
            .fixedSize(horizontal: false, vertical: true)
        } actions: {
            Button("Next") {
                let value: Int?? = switch choice {
                case .value(let value): .some(value)
                case .skip: .some(nil)
                case nil: nil
                }
                if let value { Task { await store.answer(value) } }
            }
            .buttonStyle(PrimaryButtonStyle())
            .disabled(choice == nil)
            .accessibilityIdentifier("next")
        }
    }
}

struct OptionRow: View {
    let label: Text
    let selected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 16) {
                Image(systemName: selected ? "largecircle.fill.circle" : "circle")
                    .font(.title2)
                    .foregroundStyle(selected ? Palette.accent : Color.secondary)
                    .accessibilityHidden(true)
                label
                    .font(.title3)
                    .foregroundStyle(Color.primary)
                    .multilineTextAlignment(.leading)
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 14)
            .frame(maxWidth: .infinity, minHeight: 64, alignment: .leading)
            .background(selected ? Palette.selection : Color(UIColor.secondarySystemBackground),
                        in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous)
                .strokeBorder(selected ? Palette.accent : Color.clear, lineWidth: 2))
            .contentShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }
}

/// The crisis list for the session's jurisdiction and locale. The same screen follows a flagged item and ends the
/// session, so it never says why it appears.
struct CrisisListScreen: View {
    let store: SessionStore
    let live: LiveSession

    var body: some View {
        ParticipantFrame(step: nil, pin: live.pin, onOperator: store.operatorTookOver) {
            VStack(alignment: .leading, spacing: 20) {
                Text("If you want to talk to someone")
                    .font(.largeTitle.weight(.semibold))
                    .accessibilityAddTraits(.isHeader)
                    .accessibilityIdentifier("crisisList")
                ForEach(live.rules.crisisList?.entries ?? [], id: \.self) { entry in
                    VStack(alignment: .leading, spacing: 6) {
                        Text(entry.name).font(.title2.weight(.semibold))
                        Text(entry.contact).font(.title3)
                        if let text = entry.url, let url = URL(string: text) {
                            Link(text, destination: url).font(.title3).tint(Palette.accent)
                        }
                    }
                    .padding(20)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color(UIColor.secondarySystemBackground),
                                in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                    .accessibilityElement(children: .combine)
                }
            }
            .fixedSize(horizontal: false, vertical: true)
        } actions: {
            Button("Continue", action: store.continueAfterCrisisList)
                .buttonStyle(PrimaryButtonStyle())
                .accessibilityIdentifier("continue")
        }
    }
}

struct MessageScreen: View {
    let store: SessionStore
    let live: LiveSession
    let message: LocalizedStringKey

    var body: some View {
        ParticipantFrame(step: nil, pin: live.pin, onOperator: store.operatorTookOver) {
            Text(message)
                .font(.largeTitle.weight(.semibold))
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 120)
                .accessibilityIdentifier("message")
        } actions: {
            EmptyView()
        }
    }
}

struct ThankYouScreen: View {
    let store: SessionStore
    let live: LiveSession
    @State private var exporting = false
    /// The publish runs once; a second tap shows the same export again.
    @State private var saved: ExportResult?
    @State private var result: ExportResult?

    var body: some View {
        ParticipantFrame(step: nil, pin: live.pin, onOperator: store.operatorTookOver) {
            VStack(alignment: .leading, spacing: 24) {
                Text("Thank you.")
                    .font(.largeTitle.weight(.semibold))
                    .accessibilityIdentifier("thankYou")
                Text(live.opened.administration == .supervised
                     ? "Please give the iPad back to the researcher."
                     : "You can now give the iPad back to the study team, or save the session to Files.")
                    .font(.title2)
            }
            .fixedSize(horizontal: false, vertical: true)
            .padding(.top, 120)
        } actions: {
            if live.opened.administration == .selfAdministered {
                Button {
                    if let saved {
                        result = saved
                        return
                    }
                    exporting = true
                    Task {
                        saved = await store.export(live.opened.sessionID)
                        result = saved
                        exporting = false
                    }
                } label: {
                    if exporting { ProgressView() } else { Text("Save to Files") }
                }
                .buttonStyle(PrimaryButtonStyle())
                .disabled(exporting)
                .accessibilityIdentifier("saveToFiles")
            }
        }
        .sheet(item: $result) { result in ExportScreen(result: result) }
    }
}
