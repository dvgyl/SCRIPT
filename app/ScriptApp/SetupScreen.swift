import ScriptCore
import SwiftUI

/// What the operator enters in Setup. `session` turns it into line 0, or lists what is missing.
struct SetupDraft {
    var administration: Administration = .supervised
    var participant = ""
    var ageBand: AgeBand = .youngAdult
    var jurisdiction = ""
    var locale = ""
    var site = ""
    var hand: Hand = .right
    var visitCode = "V1"
    var visitNumber = 1
    var attestor = UserDefaults.standard.string(forKey: "lastAttestor") ?? ""
    var documents: Set<DocumentRef> = []
    var mounting: MountingCode = "M1"
    var motionID = UserDefaults.standard.string(forKey: "lastMotionID") ?? "nextdraw-8511"
    var motionVersion = UserDefaults.standard.string(forKey: "lastMotionVersion") ?? ""
    var asset = UserDefaults.standard.string(forKey: "lastAsset") ?? ""
    var protector: Protector = .none
    var nib: Nib = .new
    var pencil: PencilModel = .pro
    var measured: MillimetreScale?

    enum Problem: Hashable {
        case participant, attestor, motion, asset, visit, documents, scale
    }

    init(configuration: StudyConfiguration, input: InputKind) {
        jurisdiction = configuration.jurisdictions.first?.rawValue ?? ""
        locale = configuration.locales.keys.sorted().first ?? ""
        site = configuration.sites.first?.rawValue ?? ""
        administration = configuration.administration.first ?? .supervised
        if input == .directTouchAsPen { pencil = .none }
    }

    func consent(_ configuration: StudyConfiguration) -> StudyConfiguration.ConsentEntry? {
        configuration.consentModel[ageBand.rawValue]
    }

    /// The motion system of a bench session, or nil while its identifier or its version is empty.
    var motion: DocumentRef? {
        DocumentRef(id: motionID.trimmingCharacters(in: .whitespaces),
                    version: motionVersion.trimmingCharacters(in: .whitespaces))
    }

    func problems(purpose: Purpose, configuration: StudyConfiguration, scale: MillimetreScale?) -> Set<Problem> {
        var problems = Set<Problem>()
        if purpose == .study {
            if ParticipantCode(participant) == nil { problems.insert(.participant) }
            if Code(visitCode) == nil || visitNumber < 1 { problems.insert(.visit) }
            if let consent = consent(configuration), !Set(consent.documents).isSubset(of: documents) {
                problems.insert(.documents)
            }
        }
        if purpose == .bench {
            if motion == nil { problems.insert(.motion) }
        } else if Code(attestor) == nil {
            problems.insert(.attestor)
        }
        if Code(asset) == nil { problems.insert(.asset) }
        if scale == nil { problems.insert(.scale) }
        return problems
    }

    /// Line 0 of a new session, or nil while a field is missing. The header names the last qualification of this
    /// iPad and Pencil in `qualifications`.
    func session(purpose: Purpose, configuration: StudyConfiguration, device: DeviceFacts, contract: BundledContract,
                 scale: MillimetreScale?, qualifications: QualificationRegistry) -> SessionOpened? {
        let now = Clock.now
        guard problems(purpose: purpose, configuration: configuration, scale: scale).isEmpty, let scale,
              let asset = Code(asset),
              let subject = subject(purpose: purpose, configuration: configuration, programs: contract.benchPrograms,
                                    at: now)
        else { return nil }
        return SessionOpened(
            purpose: purpose, sessionID: SessionID(), openedAt: now, openedUptime: Clock.uptime, boot: Clock.boot,
            app: device.app,
            device: DeviceInfo(model: device.model, os: device.os, nativeScale: device.nativeScale, asset: asset),
            surface: Surface(protector: protector, nib: nib),
            qualification: qualifications.last(asset: asset, pencil: pencil), scale: scale,
            pencil: pencil, channels: ChannelPlan.plan(pencil: pencil, hoverSupported: pencil != .none),
            input: device.input, subject: subject, battery: contract.battery.reference
        )
    }

    /// Who the session records. A bench session records the mounting of device P under the bundled protocol. A
    /// qualification session has no participant: it records the code `QUAL`, an adult attestation by the operator,
    /// and no consent document.
    private func subject(purpose: Purpose, configuration: StudyConfiguration, programs: BenchPrograms,
                         at now: WallTime) -> SessionSubject? {
        if purpose == .bench {
            guard let motion else { return nil }
            return .bench(BenchFacts(role: .pencil, mounting: mounting, motion: motion, protocol: programs.protocol))
        }
        guard let attestor = Code(attestor), let site = Code(site) ?? configuration.sites.first,
              let jurisdiction = Jurisdiction(jurisdiction) ?? configuration.jurisdictions.first,
              let locale = LocaleTag(locale)
        else { return nil }
        let study = purpose == .study
        let administration = study ? administration : .supervised
        let ageBand = study ? ageBand : .youngAdult
        guard let participant = study ? ParticipantCode(participant) : "QUAL",
              let visit = study ? Code(visitCode).flatMap({ Visit(id: $0, number: visitNumber) }) : Visit(id: "QUAL", number: 1),
              let entry = configuration.consentModel[ageBand.rawValue]
        else { return nil }
        return .participant(ParticipantFacts(
            participant: participant, administration: administration, study: configuration.studyRef(site: site),
            visit: visit, ageBand: ageBand, jurisdiction: jurisdiction, locale: locale,
            consent: Consent(model: study ? entry.model : .adult,
                             attestedBy: Attestor(code: attestor, role: administration == .supervised ? .operator : .enrolment),
                             attestedAt: now, documents: study ? entry.documents : [],
                             waiverReference: study ? entry.waiverReference : nil),
            ethics: configuration.ethics
        ))
    }
}

struct SetupScreen: View {
    let store: SessionStore
    let purpose: Purpose
    @State private var draft: SetupDraft

    init(store: SessionStore, purpose: Purpose) {
        self.store = store
        self.purpose = purpose
        _draft = State(initialValue: SetupDraft(configuration: store.activeConfiguration, input: store.device.input))
    }

    private var configuration: StudyConfiguration { store.activeConfiguration }
    private var scale: MillimetreScale? { store.device.tableScale ?? draft.measured }
    private var problems: Set<SetupDraft.Problem> {
        draft.problems(purpose: purpose, configuration: configuration, scale: scale)
    }

    var body: some View {
        Form {
            if purpose == .study { participantSection; consentSection }
            if purpose == .bench { benchSection }
            deviceSection
            scaleSection
            Section {
                Button(startTitle, action: start)
                    .disabled(!problems.isEmpty)
                    .accessibilityIdentifier("startSession")
            }
        }
        .navigationTitle(title)
        .scrollDismissesKeyboard(.interactively)
    }

    private var title: LocalizedStringKey {
        switch purpose {
        case .study: "New session"
        case .qualification: "Device qualification"
        case .bench: "Bench session"
        }
    }

    private var startTitle: LocalizedStringKey {
        switch purpose {
        case .study: "Start the session"
        case .qualification: "Start the qualification"
        case .bench: "Start the bench session"
        }
    }

    /// The facts of the mounting. The protocol version and digest come from the bundled programs file.
    private var benchSection: some View {
        let ref = store.contract.benchPrograms.protocol
        return Section("Bench") {
            Picker("Mounting", selection: $draft.mounting) {
                ForEach(MountingCode.all, id: \.self) { Text(verbatim: $0.rawValue).tag($0) }
            }
            .pickerStyle(.segmented)
            .accessibilityIdentifier("mounting")
            textField("Motion system", text: $draft.motionID, id: "motionID")
            textField("Motion system version", text: $draft.motionVersion, id: "motionVersion")
            LabeledContent("Protocol") {
                Text(verbatim: "\(ref.version), \(ref.sha256.hex.prefix(12))").monospaced()
            }
        }
    }

    private var participantSection: some View {
        Section("Participant") {
            if configuration.administration.count > 1 {
                Picker("Administration", selection: $draft.administration) {
                    ForEach(configuration.administration, id: \.self) { Text($0.name).tag($0) }
                }
                .pickerStyle(.segmented)
                .accessibilityIdentifier("administration")
            }
            codeField("Participant code", text: $draft.participant, id: "participantCode",
                      invalid: problems.contains(.participant) && !draft.participant.isEmpty)
            Picker("Age band", selection: $draft.ageBand) {
                ForEach(AgeBand.allCases, id: \.self) { Text(verbatim: $0.rawValue).tag($0) }
            }
            .pickerStyle(.segmented)
            .accessibilityIdentifier("ageBand")
            Picker("Jurisdiction", selection: $draft.jurisdiction) {
                ForEach(configuration.jurisdictions, id: \.self) { Text(verbatim: $0.rawValue).tag($0.rawValue) }
            }
            Picker("Language", selection: $draft.locale) {
                ForEach(configuration.locales.keys.sorted(), id: \.self) { Text(verbatim: $0).tag($0) }
            }
            if configuration.sites.count > 1 {
                Picker("Site", selection: $draft.site) {
                    ForEach(configuration.sites, id: \.self) { Text(verbatim: $0.rawValue).tag($0.rawValue) }
                }
            }
            Picker("Writing hand", selection: $draft.hand) {
                Text("Right").tag(Hand.right)
                Text("Left").tag(Hand.left)
            }
            .pickerStyle(.segmented)
            codeField("Visit code", text: $draft.visitCode, id: "visitCode", invalid: problems.contains(.visit))
            Stepper("Visit \(draft.visitNumber)", value: $draft.visitNumber, in: 1...99)
        }
    }

    @ViewBuilder
    private var consentSection: some View {
        let entry = draft.consent(configuration)
        Section("Consent") {
            if let entry {
                LabeledContent("Model") { Text(verbatim: entry.model.rawValue) }
                ForEach(entry.documents, id: \.self) { document in
                    let checked = draft.documents.contains(document)
                    Button {
                        if checked { draft.documents.remove(document) } else { draft.documents.insert(document) }
                    } label: {
                        HStack(spacing: 12) {
                            Image(systemName: checked ? "checkmark.square.fill" : "square")
                                .font(.title3)
                                .foregroundStyle(checked ? Palette.accent : Color.secondary)
                                .accessibilityHidden(true)
                            Text("\(document.id), version \(document.version)")
                                .foregroundStyle(Color.primary)
                            Spacer(minLength: 0)
                        }
                        .contentShape(Rectangle())
                    }
                    .accessibilityAddTraits(checked ? .isSelected : [])
                    .accessibilityIdentifier("document-\(document.id)")
                }
            }
            codeField(draft.administration == .supervised ? "Operator code" : "Enrolment record code",
                      text: $draft.attestor, id: "attestor", invalid: problems.contains(.attestor) && !draft.attestor.isEmpty)
        }
    }

    private var deviceSection: some View {
        Section("Device") {
            if purpose == .qualification {
                codeField("Operator code", text: $draft.attestor, id: "attestor",
                          invalid: problems.contains(.attestor) && !draft.attestor.isEmpty)
            }
            codeField("Device asset code", text: $draft.asset, id: "asset",
                      invalid: problems.contains(.asset) && !draft.asset.isEmpty)
            Picker("Screen protector", selection: $draft.protector) {
                Text("None").tag(Protector.none)
                Text("Glossy").tag(Protector.glossy)
                Text("Matte").tag(Protector.matte)
                Text("Paper-like").tag(Protector.paperlike)
                Text("Not known").tag(Protector.unknown)
            }
            Picker("Pencil tip", selection: $draft.nib) {
                Text("New").tag(Nib.new)
                Text("Used").tag(Nib.used)
                Text("Not known").tag(Nib.unknown)
            }
            if store.device.input == .directTouchAsPen {
                LabeledContent("Pencil") { Text(PencilModel.none.name) }
            } else {
                Picker("Pencil", selection: $draft.pencil) {
                    ForEach([PencilModel.pro, .second, .usbC], id: \.self) { Text($0.name).tag($0) }
                }
            }
            LabeledContent("Last qualification") { lastQualification }
        }
    }

    @ViewBuilder
    private var lastQualification: some View {
        if let asset = Code(draft.asset), let last = store.qualifications.last(asset: asset, pencil: draft.pencil),
           let date = try? Date(last.at.text, strategy: .iso8601) {
            Text("\(date, format: .dateTime.day().month().year()), session \(String(last.sessionID.description.prefix(8)))")
        } else {
            Text("None for this iPad and Pencil")
        }
    }

    private var scaleSection: some View {
        Section("Scale") {
            if let scale = store.device.tableScale, let entry = DeviceTable.density(model: store.device.model) {
                LabeledContent("From the device table") {
                    Text("\(entry.name), \(Int(entry.ppi)) ppi")
                }
                LabeledContent("Millimetres per point") {
                    Text(scale.mmPerPoint ?? 0, format: .number.precision(.fractionLength(4)))
                }
            } else {
                NavigationLink {
                    MeasurementScreen(scale: $draft.measured)
                } label: {
                    LabeledContent("Measure the screen") {
                        if let mm = draft.measured?.mmPerPoint {
                            Text("\(mm, format: .number.precision(.fractionLength(4))) mm per point")
                        } else {
                            Text("Needed").foregroundStyle(.red)
                        }
                    }
                }
                .accessibilityIdentifier("measure")
            }
        }
    }

    private func textField(_ title: LocalizedStringKey, text: Binding<String>, id: String) -> some View {
        LabeledContent {
            TextField(title, text: text)
                .multilineTextAlignment(.trailing)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .accessibilityIdentifier(id)
        } label: {
            Text(title)
        }
    }

    private func codeField(_ title: LocalizedStringKey, text: Binding<String>, id: String, invalid: Bool) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            LabeledContent {
                TextField(title, text: text)
                    .multilineTextAlignment(.trailing)
                    .textInputAutocapitalization(.characters)
                    .autocorrectionDisabled()
                    .accessibilityIdentifier(id)
            } label: {
                Text(title)
            }
            if invalid {
                Text("Use letters, digits, and hyphens only.")
                    .font(.footnote)
                    .foregroundStyle(.red)
            }
        }
    }

    private func start() {
        guard let opened = draft.session(purpose: purpose, configuration: configuration, device: store.device,
                                         contract: store.contract, scale: scale, qualifications: store.qualifications)
        else { return }
        UserDefaults.standard.set(draft.asset, forKey: "lastAsset")
        if purpose == .bench {
            UserDefaults.standard.set(draft.motionID, forKey: "lastMotionID")
            UserDefaults.standard.set(draft.motionVersion, forKey: "lastMotionVersion")
            store.openBench(opened)
        } else {
            UserDefaults.standard.set(draft.attestor, forKey: "lastAttestor")
            store.open(opened, configuration: configuration, dominant: draft.hand)
        }
    }
}

/// The reference measurement for an iPad that the device table does not confirm. The operator changes the bar until
/// it is 100 mm on a steel rule, or the outline until it matches an ID-1 card. The drawing never scales with text.
struct MeasurementScreen: View {
    @Binding var scale: MillimetreScale?
    @Environment(\.dismiss) private var dismiss
    @State private var object: ReferenceObject = .steelRule
    @State private var points = start(.steelRule)

    static let ruleMM = 100.0
    static let cardHeightMM = 53.98

    /// The starting length at the density of most iPads, 264 pixels per inch at 2 pixels per point.
    static func start(_ object: ReferenceObject) -> Double {
        let mm = object == .steelRule ? ruleMM : ReferenceObject.idCardWidthMM
        return (mm / (25.4 * 2 / 264)).rounded()
    }

    var body: some View {
        ScrollView {
            measurement.padding(32)
        }
        .navigationTitle("Measure the screen")
    }

    private var measurement: some View {
        VStack(spacing: 24) {
            Picker("Reference", selection: $object) {
                Text("Steel rule").tag(ReferenceObject.steelRule)
                Text("ID card").tag(ReferenceObject.idCard)
            }
            .pickerStyle(.segmented)
            .frame(maxWidth: 420)
            .onChange(of: object) { _, object in points = Self.start(object) }
            Text(object == .steelRule
                 ? "Lay the rule along the bar. Change the length until the bar is exactly 100 mm."
                 : "Lay an ID-1 card on the outline. Change the size until the outline matches the edges of the card.")
                .font(.body)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
            reference
                .frame(maxWidth: .infinity, minHeight: 360)
                .accessibilityElement()
                .accessibilityLabel(object == .steelRule ? Text("Measurement bar") : Text("Card outline"))
                .accessibilityValue(Text("\(points, format: .number.precision(.fractionLength(1))) points"))
            Text("\(points, format: .number.precision(.fractionLength(1))) points")
                .monospacedDigit()
            HStack(spacing: 12) {
                step(-10)
                step(-1)
                step(1)
                step(10)
            }
            Slider(value: $points, in: 200...760, step: 0.5) { Text("Length") }
                .frame(maxWidth: 520)
            Button("Use this measurement") {
                scale = object == .steelRule
                    ? .steelRule(measuredMM: Self.ruleMM, drawnPoints: points)
                    : .idCard(drawnPoints: points)
                dismiss()
            }
            .buttonStyle(.borderedProminent)
            .accessibilityIdentifier("useMeasurement")
        }
    }

    @ViewBuilder
    private var reference: some View {
        switch object {
        case .steelRule:
            ZStack {
                Rectangle().fill(Color.primary).frame(width: points, height: 4)
                HStack(spacing: 0) {
                    Rectangle().fill(Color.primary).frame(width: 2, height: 40)
                    Spacer(minLength: 0)
                    Rectangle().fill(Color.primary).frame(width: 2, height: 40)
                }
                .frame(width: points + 2)
            }
        case .idCard:
            RoundedRectangle(cornerRadius: points * 3.18 / ReferenceObject.idCardWidthMM)
                .strokeBorder(Color.primary, lineWidth: 2)
                .frame(width: points, height: points * Self.cardHeightMM / ReferenceObject.idCardWidthMM)
        }
    }

    private func step(_ delta: Double) -> some View {
        Button {
            points = min(760, max(200, points + delta))
        } label: {
            Text(delta > 0 ? "+\(Int(delta))" : "\(Int(delta))").monospacedDigit().frame(minWidth: 44, minHeight: 44)
        }
        .buttonStyle(.bordered)
        .accessibilityLabel(delta > 0 ? Text("Longer by \(Int(delta)) points") : Text("Shorter by \(Int(-delta)) points"))
    }
}

extension MountingCode {
    /// The mountings of the bench protocol, M1 to M8 (section 6.1).
    static let all: [MountingCode] = (1...8).compactMap { MountingCode("M\($0)") }
}
