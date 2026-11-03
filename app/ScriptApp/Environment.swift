import Foundation
import ScriptCore
import UIKit

/// The contract files that the app ships: the battery, the instruments, and the demo configuration.
struct BundledContract: Sendable {
    let battery: Battery
    let instruments: InstrumentSet
    let demo: StudyConfiguration

    enum LoadError: Error { case missing(String), invalid(String) }

    init(bundle: Bundle = .main) throws {
        func data(_ name: String) throws -> Data {
            guard let url = bundle.url(forResource: name, withExtension: "json") else { throw LoadError.missing(name) }
            return try Data(contentsOf: url)
        }
        do {
            battery = try Battery(data: try data("battery"))
            instruments = try InstrumentSet(data: try data("instruments"))
            demo = try StudyConfiguration(data: try data("demo"))
        } catch let error as LoadError {
            throw error
        } catch {
            throw LoadError.invalid(String(describing: error))
        }
    }
}

/// The configurations on this device: the shipped demo and every imported file, kept by SHA-256 so that a session
/// always finds the configuration it recorded. One of them is active for new sessions.
struct ConfigurationLibrary {
    let folder: URL
    let demo: StudyConfiguration
    private let defaults: UserDefaults
    private static let activeKey = "activeConfigurationSHA256"

    init(folder: URL, demo: StudyConfiguration, defaults: UserDefaults = .standard) {
        self.folder = folder
        self.demo = demo
        self.defaults = defaults
    }

    var active: StudyConfiguration {
        guard let sha = defaults.string(forKey: Self.activeKey), let config = configuration(sha: sha) else { return demo }
        return config
    }

    func configuration(sha: String) -> StudyConfiguration? {
        if sha == demo.sha256.hex { return demo }
        guard let data = try? Data(contentsOf: folder.appendingPathComponent("\(sha).json")) else { return nil }
        return try? StudyConfiguration(data: data)
    }

    enum Refusal: Error, Hashable {
        case unreadable
        case decode(DecodeError)
        case problems([ConfigProblem])
        case exportKey
    }

    /// Checks a file with `ScriptCore`, keeps it, and makes it active. A file that fails any check is refused.
    func importConfiguration(_ data: Data, battery: Battery, instruments: InstrumentSet) -> Result<StudyConfiguration, Refusal> {
        let config: StudyConfiguration
        do throws(DecodeError) {
            config = try StudyConfiguration(data: data)
        } catch {
            return .failure(.decode(error))
        }
        let problems = validate(config: config, battery: battery, instruments: instruments)
        guard problems.isEmpty else { return .failure(.problems(problems)) }
        guard (try? ExportPolicy(configuration: config)) != nil else { return .failure(.exportKey) }
        do {
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            try data.write(to: folder.appendingPathComponent("\(config.sha256.hex).json"), options: .atomic)
        } catch {
            return .failure(.unreadable)
        }
        defaults.set(config.sha256.hex, forKey: Self.activeKey)
        return .success(config)
    }

    func useDemo() { defaults.removeObject(forKey: Self.activeKey) }
}

/// Facts about this device and this process that a session records.
struct DeviceFacts: Sendable {
    /// The `hw.machine` identifier. The simulator reports the model that it simulates.
    var model: String
    var os: String
    var nativeScale: Double
    var app: AppInfo
    /// Direct touch is recorded as Pencil input only in a DEBUG build that runs with `-ScriptUITest`.
    var input: InputKind

    @MainActor static func current() -> DeviceFacts {
        let info = Bundle.main.infoDictionary ?? [:]
        let scene = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.first
        return DeviceFacts(
            model: modelIdentifier(),
            os: "iPadOS \(UIDevice.current.systemVersion)",
            nativeScale: Double(scene?.screen.nativeScale ?? 2),
            app: AppInfo(version: info["CFBundleShortVersionString"] as? String ?? "0",
                         build: info["CFBundleVersion"] as? String ?? "0",
                         sourceRevision: info["SCRIPTSourceRevision"] as? String ?? "unknown"),
            input: LaunchMode.directTouchAsPen ? .directTouchAsPen : .pencil
        )
    }

    private static func modelIdentifier() -> String {
        if let simulated = ProcessInfo.processInfo.environment["SIMULATOR_MODEL_IDENTIFIER"] { return simulated }
        var system = utsname()
        uname(&system)
        return withUnsafeBytes(of: &system.machine) { raw in
            String(decoding: raw.prefix { $0 != 0 }, as: UTF8.self)
        }
    }

    /// The scale from the device table, when the table confirms the pixel density of this model.
    var tableScale: MillimetreScale? { tableScale(density: DeviceTable.density(model:)) }

    func tableScale(density: (String) -> PixelDensity?) -> MillimetreScale? {
        guard !LaunchMode.measureScale, density(model)?.ppiConfirmed == true else { return nil }
        return MillimetreScale.lookup(model: model, nativeScale: nativeScale)
    }
}

enum LaunchMode {
    static var directTouchAsPen: Bool { debugArgument("-ScriptUITest") }

    /// Setup asks for the reference measurement even on a model in the device table, so a UI test reaches it.
    static var measureScale: Bool { debugArgument("-ScriptMeasureScale") }

    private static func debugArgument(_ name: String) -> Bool {
        #if DEBUG
        ProcessInfo.processInfo.arguments.contains(name)
        #else
        false
        #endif
    }
}

/// The boot and the clocks of this launch. Samples and app events use the same uptime clock.
enum Clock {
    static var uptime: Double { ProcessInfo.processInfo.systemUptime }
    static var now: WallTime { WallTime(Date()) }

    /// The boot-session UUID of the kernel. A random value stands in only when the kernel does not give one.
    static let boot: BootID = {
        var size = 0
        if sysctlbyname("kern.bootsessionuuid", nil, &size, nil, 0) == 0, size > 0 {
            var buffer = [CChar](repeating: 0, count: size)
            if sysctlbyname("kern.bootsessionuuid", &buffer, &size, nil, 0) == 0,
               let uuid = UUID(uuidString: String(decoding: buffer.prefix { $0 != 0 }.map(UInt8.init), as: UTF8.self)) {
                return BootID(uuid)
            }
        }
        return BootID(UUID())
    }()

    static var launch: Storage.Launch { Storage.Launch(boot: boot, uptime: uptime, at: now) }
}

/// The last qualification session of each device asset and Pencil model. It holds no participant data.
struct QualificationRegistry {
    private let defaults: UserDefaults
    private static let key = "qualifications"

    init(defaults: UserDefaults = .standard) { self.defaults = defaults }

    private static func slot(asset: Code, pencil: PencilModel) -> String { "\(asset.rawValue)|\(pencil.rawValue)" }

    func last(asset: Code, pencil: PencilModel) -> QualificationRef? {
        guard let all = defaults.dictionary(forKey: Self.key) as? [String: [String]],
              let pair = all[Self.slot(asset: asset, pencil: pencil)], pair.count == 2,
              let uuid = UUID(uuidString: pair[0]), let at = try? Date(pair[1], strategy: .iso8601)
        else { return nil }
        return QualificationRef(sessionID: SessionID(uuid), at: WallTime(at))
    }

    func record(_ opened: SessionOpened) {
        var all = defaults.dictionary(forKey: Self.key) as? [String: [String]] ?? [:]
        all[Self.slot(asset: opened.device.asset, pencil: opened.pencil)] = [opened.sessionID.description,
                                                                             opened.openedAt.text]
        defaults.set(all, forKey: Self.key)
    }
}
