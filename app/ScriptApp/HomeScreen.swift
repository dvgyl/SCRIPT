import ScriptCore
import SwiftUI
import UniformTypeIdentifiers

struct RootView: View {
    @Bindable var store: SessionStore

    var body: some View {
        Group {
            if let live = store.live, !live.away {
                SessionView(store: store, live: live)
            } else {
                HomeScreen(store: store)
            }
        }
        .tint(Palette.accent)
        .alert(item: $store.notice) { notice in
            Alert(title: Text(notice.title), message: Text(notice.message))
        }
    }
}

extension Notice {
    var title: LocalizedStringKey {
        switch self {
        case .storageFailure: "Storage problem"
        case .lowStorage: "Not enough free storage"
        case .configurationMissing: "Configuration not on this iPad"
        case .sessionOpen: "A session is open"
        case .exportFailed: "Export failed"
        case .failed: "Something went wrong"
        }
    }

    var message: String {
        switch self {
        case .storageFailure:
            String(localized: "The iPad could not save the recording. The session stopped. Every line saved before the failure is kept.")
        case .lowStorage:
            String(localized: "A session needs at least 500 MB of free storage. Free some storage on this iPad, and try again.")
        case .configurationMissing:
            String(localized: "Import the configuration that this session used, and try again.")
        case .sessionOpen:
            String(localized: "Finish or end the open session first.")
        case .exportFailed:
            String(localized: "The export could not be verified. The session stays on this iPad.")
        case .failed(let text):
            text
        }
    }
}

struct HomeScreen: View {
    let store: SessionStore
    @State private var importing = false
    @State private var imported: ImportOutcome?
    @State private var exportResult: ExportResult?
    @State private var exportingID: String?
    @State private var askingPIN = false
    /// A bench session runs under no configuration, so its Setup shows no demo banner.
    @State private var benchSetupShown = false

    var body: some View {
        NavigationStack {
            List {
                configurationSection
                Section {
                    NavigationLink("New session") { SetupScreen(store: store, purpose: .study) }
                        .accessibilityIdentifier("newSession")
                    NavigationLink("Device qualification") { SetupScreen(store: store, purpose: .qualification) }
                        .accessibilityIdentifier("newQualification")
                    NavigationLink("Bench session") {
                        SetupScreen(store: store, purpose: .bench)
                            .onAppear { benchSetupShown = true }
                            .onDisappear { benchSetupShown = false }
                    }
                    .accessibilityIdentifier("newBench")
                }
                .disabled(store.live != nil)
                if store.listing.isEmpty {
                    Section("Sessions") { Text("No sessions on this iPad.").foregroundStyle(.secondary) }
                } else {
                    Section("Sessions") {
                        ForEach(store.listing) { row in sessionRow(row) }
                    }
                }
                Section("Withdrawal") {
                    NavigationLink("Withdraw a participant") { WithdrawalScreen(store: store) }
                        .disabled(store.live != nil)
                        .accessibilityIdentifier("withdraw")
                    NavigationLink("Verify a withdrawal") { VerifyWithdrawalScreen(store: store) }
                        .accessibilityIdentifier("verifyWithdrawal")
                }
            }
            .navigationTitle("SCRIPT Capture")
            .fileImporter(isPresented: $importing, allowedContentTypes: [.json]) { result in
                imported = ImportOutcome(result.map { url in
                    let scoped = url.startAccessingSecurityScopedResource()
                    defer { if scoped { url.stopAccessingSecurityScopedResource() } }
                    return (try? Data(contentsOf: url)).map(store.importConfiguration) ?? .failure(.unreadable)
                })
            }
            .sheet(item: $imported) { outcome in ImportResultScreen(outcome: outcome) }
            .sheet(item: $exportResult) { result in ExportScreen(result: result) }
            .toolbar {
                if store.activeConfiguration.operatorPin != nil, !store.codesUnlocked {
                    ToolbarItem(placement: .primaryAction) {
                        Button("Show participant codes") { askingPIN = true }
                    }
                }
            }
            .sheet(isPresented: $askingPIN) {
                if let pin = store.activeConfiguration.operatorPin { PINSheet(pin: pin, onUnlock: store.unlockCodes) }
            }
        }
        .demoBanner(store.activeConfiguration.demo && !benchSetupShown)
    }

    private var configurationSection: some View {
        let config = store.activeConfiguration
        return Section("Configuration") {
            LabeledContent("Study") { Text(verbatim: config.study.id.rawValue) }
            LabeledContent("Version") { Text(verbatim: config.study.version) }
            LabeledContent("Digest") { Text(verbatim: String(config.sha256.hex.prefix(12))).monospaced() }
            Button("Import a configuration") { importing = true }
                .disabled(store.live != nil)
            if !config.demo {
                Button("Use the demo configuration", action: store.useDemoConfiguration)
                    .disabled(store.live != nil)
            }
        }
    }

    /// One line when it fits, else the facts under the name and the controls under the facts, as at the largest
    /// text sizes.
    private func sessionRow(_ row: Listed) -> some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 12) {
                rowInfo(row)
                Spacer()
                rowControls(row)
            }
            VStack(alignment: .leading, spacing: 8) {
                rowInfo(row)
                HStack(spacing: 12) { rowControls(row) }
            }
        }
        .accessibilityElement(children: .contain)
    }

    private func rowInfo(_ row: Listed) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            // An identifier stays on one line, because a line break would hyphenate it into another name.
            Text(verbatim: store.title(of: row)).font(.body.weight(.medium)).lineLimit(1).minimumScaleFactor(0.4)
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 6) { rowFacts(row) }
                VStack(alignment: .leading, spacing: 2) { rowFacts(row) }
            }
            .font(.subheadline)
            .foregroundStyle(.secondary)
        }
    }

    @ViewBuilder
    private func rowFacts(_ row: Listed) -> some View {
        if let purpose = row.purpose, purpose != .study { Text(purpose.name).fixedSize() }
        if let administration = row.administration { Text(administration.name).fixedSize() }
        if let openedAt = row.openedAt, let date = try? Date(openedAt, strategy: .iso8601) {
            Text(date, format: .dateTime.day().month().hour().minute()).fixedSize()
        }
        if let encrypted = row.encrypted { Text(encrypted ? "Encrypted" : "Not encrypted").fixedSize() }
    }

    @ViewBuilder
    private func rowControls(_ row: Listed) -> some View {
        StatusBadge(status: row.status)
        switch row.status {
        case .open:
            Button("Return") { store.returnToSession() }.buttonStyle(.bordered)
        case .interrupted:
            if let id = row.sessionID {
                Button("Resume") { store.resume(id) }.buttonStyle(.bordered).disabled(store.live != nil)
            }
        case .closed:
            if let id = row.sessionID {
                exportButton(row, "Export") { await store.export(id) }
            }
        case .damaged:
            if let file = row.damagedFile {
                exportButton(row, "Export damaged journal") { await store.exportDamaged(file) }
            }
        case .exported:
            EmptyView()
        }
    }

    private func exportButton(_ row: Listed, _ title: LocalizedStringKey,
                              _ export: @escaping () async -> ExportResult?) -> some View {
        Button {
            exportingID = row.id
            Task {
                exportResult = await export()
                exportingID = nil
            }
        } label: {
            if exportingID == row.id { ProgressView() } else { Text(title) }
        }
        .buttonStyle(.bordered)
        .disabled(exportingID != nil)
        .accessibilityIdentifier("export-\(store.title(of: row))")
    }
}

struct StatusBadge: View {
    let status: Listed.Status

    var body: some View {
        Text(label)
            .font(.caption.weight(.semibold))
            .padding(.horizontal, 10)
            .padding(.vertical, 4)
            .background(Color(UIColor.tertiarySystemFill), in: Capsule())
    }

    private var label: LocalizedStringKey {
        switch status {
        case .open: "Open"
        case .interrupted: "Interrupted"
        case .closed: "Closed"
        case .exported: "Exported"
        case .damaged: "Damaged"
        }
    }
}

struct ImportOutcome: Identifiable {
    let id = UUID()
    let result: Result<StudyConfiguration, ConfigurationLibrary.Refusal>

    init(_ picked: Result<Result<StudyConfiguration, ConfigurationLibrary.Refusal>, any Error>) {
        switch picked {
        case .success(let result): self.result = result
        case .failure: result = .failure(.unreadable)
        }
    }
}

/// The imported configuration, or every problem that refuses it.
struct ImportResultScreen: View {
    let outcome: ImportOutcome
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                switch outcome.result {
                case .success(let config):
                    Section {
                        LabeledContent("Study") { Text(verbatim: config.study.id.rawValue) }
                        LabeledContent("Version") { Text(verbatim: config.study.version) }
                        LabeledContent("Digest") { Text(verbatim: String(config.sha256.hex.prefix(12))).monospaced() }
                    } footer: {
                        Text("New sessions use this configuration.")
                    }
                case .failure(let refusal):
                    Section("The configuration was refused") {
                        ForEach(problems(refusal), id: \.self) { Text(verbatim: $0) }
                    }
                }
            }
            .navigationTitle(isSuccess ? "Configuration imported" : "Configuration refused")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
        }
    }

    private var isSuccess: Bool {
        if case .success = outcome.result { true } else { false }
    }

    private func problems(_ refusal: ConfigurationLibrary.Refusal) -> [String] {
        switch refusal {
        case .unreadable: [String(localized: "The file could not be read.")]
        case .decode(let error): [String(localized: "The file does not match the configuration schema: \(String(describing: error))")]
        case .problems(let problems): problems.map(\.description)
        case .exportKey: [String(localized: "export.recipientPublicKey is not a valid X25519 public key")]
        }
    }
}

/// Withdrawal: the operator enters the code twice and confirms; the app deletes every recording of that code.
struct WithdrawalScreen: View {
    let store: SessionStore
    @State private var code = ""
    @State private var again = ""
    @State private var confirming = false
    @State private var result: Storage.Withdrawal?

    private var participant: ParticipantCode? {
        code == again ? ParticipantCode(code) : nil
    }

    var body: some View {
        Form {
            Section {
                TextField("Participant code", text: $code)
                    .textInputAutocapitalization(.characters)
                    .autocorrectionDisabled()
                    .accessibilityIdentifier("withdrawCode")
                TextField("Participant code again", text: $again)
                    .textInputAutocapitalization(.characters)
                    .autocorrectionDisabled()
                    .accessibilityIdentifier("withdrawCodeAgain")
            } footer: {
                if !again.isEmpty, code != again { Text("The two codes are not the same.").foregroundStyle(.red) }
            }
            Section {
                Button("Delete every recording of this code", role: .destructive) { confirming = true }
                    .disabled(participant == nil)
                    .accessibilityIdentifier("withdrawDelete")
            }
            if let result {
                Section("Deleted") {
                    if result.sessions.isEmpty {
                        Text("No recording of this code was on this iPad. The deletion log has a line for it.")
                            .accessibilityIdentifier("withdrawResult")
                    }
                    ForEach(result.sessions, id: \.self) { id in
                        Text(verbatim: id.description).monospaced().font(.footnote)
                            .accessibilityIdentifier("withdrawResult")
                    }
                }
                if !result.unreadable.isEmpty {
                    Section {
                        ForEach(result.unreadable, id: \.self) { Text(verbatim: $0.lastPathComponent).font(.footnote) }
                    } header: {
                        Text("Journals that could not be read")
                    } footer: {
                        Text("These journals name no participant. They were not deleted.")
                    }
                }
            }
        }
        .navigationTitle("Withdraw a participant")
        .confirmationDialog("Delete every recording of this code on this iPad?", isPresented: $confirming,
                            titleVisibility: .visible) {
            Button("Delete", role: .destructive) {
                if let participant { result = store.withdraw(participant) }
                code = ""
                again = ""
            }
            .accessibilityIdentifier("withdrawConfirm")
        } message: {
            Text("This cannot be undone. Copies outside this iPad are not deleted.")
        }
    }
}

struct VerifyWithdrawalScreen: View {
    let store: SessionStore
    @State private var code = ""
    @State private var found: Bool?

    var body: some View {
        Form {
            Section {
                TextField("Participant code", text: $code)
                    .textInputAutocapitalization(.characters)
                    .autocorrectionDisabled()
                    .onChange(of: code) { found = nil }
                    .accessibilityIdentifier("verifyCode")
                Button("Check the deletion log") {
                    if let participant = ParticipantCode(code) { found = store.verifyWithdrawal(participant) }
                }
                .disabled(ParticipantCode(code) == nil)
                .accessibilityIdentifier("verifyCheck")
            }
            if let found {
                Section {
                    Label(found ? "The deletion log on this iPad has a withdrawal for this code."
                                : "The deletion log on this iPad has no withdrawal for this code.",
                          systemImage: found ? "checkmark.circle" : "xmark.circle")
                    .accessibilityIdentifier(found ? "verifyFound" : "verifyNotFound")
                }
            }
        }
        .navigationTitle("Verify a withdrawal")
    }
}
