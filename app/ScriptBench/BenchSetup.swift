import ScriptCore
import SwiftUI

/// The two session kinds of the bench Mac.
enum SessionKind: Hashable, Sendable {
    /// One mounting of the tablet under the bench protocol.
    case bench
    /// The qualification session of a mounting (bench protocol section 8.1).
    case qualification
}

/// What the operator enters in Setup. `session` turns it into line 0, or gives nil while a field is missing.
struct BenchDraft {
    var mounting: MountingCode = "M1"
    var motionID = "nextdraw-8511"
    var motionVersion = ""
    var tabletModel = TabletTable.entries.first { $0.confirmed }?.model ?? ""
    var tabletAsset = ""
    var driver = ""
    var pen: TabletPen = .proPen3
    var macAsset = ""
    var operatorCode = ""
    var protector: Protector = .none
    var nib: Nib = .new

    static let mountings = (1...8).compactMap { MountingCode("M\($0)") }

    /// The values of the last Setup on this Mac.
    static func remembered(_ defaults: UserDefaults = .standard) -> BenchDraft {
        var draft = BenchDraft()
        for (key, path) in rememberedFields {
            draft[keyPath: path] = defaults.string(forKey: key) ?? draft[keyPath: path]
        }
        return draft
    }

    func remember(_ defaults: UserDefaults = .standard) {
        for (key, path) in Self.rememberedFields { defaults.set(self[keyPath: path], forKey: key) }
    }

    private static var rememberedFields: [(String, WritableKeyPath<BenchDraft, String>)] {
        [("motionID", \.motionID), ("motionVersion", \.motionVersion), ("tabletAsset", \.tabletAsset),
         ("driver", \.driver), ("macAsset", \.macAsset), ("operatorCode", \.operatorCode)]
    }

    /// True when every field that the session kind needs is valid.
    func isComplete(_ kind: SessionKind) -> Bool {
        guard Code(tabletAsset) != nil, !driver.trimmingCharacters(in: .whitespaces).isEmpty, Code(macAsset) != nil
        else { return false }
        return switch kind {
        case .bench: motion != nil
        case .qualification: Code(operatorCode) != nil
        }
    }

    private var motion: DocumentRef? {
        DocumentRef(id: motionID.trimmingCharacters(in: .whitespaces),
                    version: motionVersion.trimmingCharacters(in: .whitespaces))
    }

    /// Line 0 of a new session. A bench session records the mounting, the motion system, and the protocol of the
    /// programs file. A qualification session has no participant: as on the iPad, it records the code `QUAL`, an adult
    /// attestation by the operator, no consent document, and the study of the demo configuration.
    func session(_ kind: SessionKind, contract: BenchContract, mac: MacFacts, qualification: QualificationRef?,
                 uptime: Double, now: WallTime, boot: BootID) -> SessionOpened? {
        guard isComplete(kind), let geometry = TabletTable.geometry(model: tabletModel), geometry.confirmed,
              let tabletAsset = Code(tabletAsset), let macAsset = Code(macAsset) else { return nil }
        let subject: SessionSubject
        switch kind {
        case .bench:
            guard let motion else { return nil }
            subject = .bench(BenchFacts(role: .tablet, mounting: mounting, motion: motion,
                                        protocol: contract.programs.protocol))
        case .qualification:
            let demo = contract.demo
            guard let attestor = Code(operatorCode), let site = demo.sites.first,
                  let jurisdiction = demo.jurisdictions.first,
                  let locale = demo.locales.keys.sorted().first.flatMap(LocaleTag.init(_:)),
                  let visit = Visit(id: "QUAL", number: 1) else { return nil }
            subject = .participant(ParticipantFacts(
                participant: "QUAL", administration: .supervised, study: demo.studyRef(site: site), visit: visit,
                ageBand: .youngAdult, jurisdiction: jurisdiction, locale: locale,
                consent: Consent(model: .adult, attestedBy: Attestor(code: attestor, role: .operator), attestedAt: now,
                                 documents: []),
                ethics: demo.ethics
            ))
        }
        return SessionOpened(
            purpose: kind == .bench ? .bench : .qualification, sessionID: SessionID(), openedAt: now,
            openedUptime: uptime, boot: boot, app: mac.app,
            device: DeviceInfo(model: mac.model, os: mac.os, nativeScale: mac.nativeScale, asset: macAsset),
            tablet: TabletInfo(model: geometry.model, asset: tabletAsset,
                               driver: driver.trimmingCharacters(in: .whitespaces), pen: pen),
            surface: Surface(protector: protector, nib: nib), qualification: qualification, scale: geometry.scale,
            pencil: .none, channels: ChannelPlan.plan(tabletPen: pen), input: .tablet, subject: subject,
            battery: contract.battery.reference
        )
    }
}

struct BenchSetup: View {
    let store: BenchStore
    let kind: SessionKind
    @State private var draft = BenchDraft.remembered()

    var body: some View {
        Form {
            if kind == .bench {
                Section("Mounting") {
                    Picker("Mounting", selection: $draft.mounting) {
                        ForEach(BenchDraft.mountings, id: \.self) { Text(verbatim: $0.rawValue).tag($0) }
                    }
                    TextField("Motion system", text: $draft.motionID)
                    TextField("Motion system version", text: $draft.motionVersion)
                }
            } else {
                Section("Operator") {
                    codeField("Operator code", text: $draft.operatorCode)
                }
            }
            Section("Tablet") {
                Picker("Model", selection: $draft.tabletModel) {
                    ForEach(TabletTable.entries.filter(\.confirmed), id: \.model) { Text(verbatim: $0.model).tag($0.model) }
                }
                Picker("Pen", selection: $draft.pen) {
                    Text("Wacom Pro Pen 3").tag(TabletPen.proPen3)
                    Text("Not known").tag(TabletPen.unknown)
                }
                codeField("Tablet asset code", text: $draft.tabletAsset)
                TextField("Driver version", text: $draft.driver)
                Picker("Surface", selection: $draft.protector) {
                    Text("None").tag(Protector.none)
                    Text("Glossy").tag(Protector.glossy)
                    Text("Matte").tag(Protector.matte)
                    Text("Paper-like").tag(Protector.paperlike)
                    Text("Not known").tag(Protector.unknown)
                }
                Picker("Nib", selection: $draft.nib) {
                    Text("New").tag(Nib.new)
                    Text("Used").tag(Nib.used)
                    Text("Not known").tag(Nib.unknown)
                }
                LabeledContent("Last qualification") { lastQualification }
            }
            Section("Mac") {
                codeField("Mac asset code", text: $draft.macAsset)
                LabeledContent("Model") { Text(verbatim: store.mac.model) }
                LabeledContent("System") { Text(verbatim: store.mac.os) }
            }
            Section {
                Button(kind == .bench ? "Start the bench session" : "Start the qualification", action: start)
                    .disabled(!draft.isComplete(kind) || store.clock != .passed)
                    .keyboardShortcut(.defaultAction)
            }
        }
        .formStyle(.grouped)
        .navigationTitle(kind == .bench ? "New bench session" : "New qualification session")
    }

    /// A code field. A code that breaks the pattern says why under the field.
    private func codeField(_ title: LocalizedStringKey, text: Binding<String>) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            TextField(title, text: text)
            if !text.wrappedValue.isEmpty, Code(text.wrappedValue) == nil {
                Text("Use letters, digits, and hyphens only.").font(.footnote).foregroundStyle(.red)
            }
        }
    }

    @ViewBuilder
    private var lastQualification: some View {
        if let asset = Code(draft.tabletAsset), let last = store.qualifications.last(asset: asset, pen: draft.pen),
           let date = last.at.date {
            Text("\(date, format: .dateTime.day().month().year().hour().minute()), session \(String(last.sessionID.description.prefix(8)))")
        } else {
            Text("None for this tablet and pen")
        }
    }

    private func start() {
        let qualification = Code(draft.tabletAsset).flatMap { store.qualifications.last(asset: $0, pen: draft.pen) }
        guard let opened = draft.session(kind, contract: store.contract, mac: store.mac, qualification: qualification,
                                         uptime: Clock.uptime, now: Clock.now, boot: Clock.boot) else { return }
        draft.remember()
        store.open(opened)
    }
}
