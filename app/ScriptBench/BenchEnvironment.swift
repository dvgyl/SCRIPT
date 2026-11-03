import AppKit
import CryptoKit
import Foundation
import ScriptCore

/// The contract files that the app ships: the battery, the instruments, the bench programs, and the demo
/// configuration, which a qualification session records as its study.
struct BenchContract: Sendable {
    let battery: Battery
    let instruments: InstrumentSet
    let programs: BenchPrograms
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
            programs = try BenchPrograms(data: try data("bench_programs"))
            demo = try StudyConfiguration(data: try data("demo"))
        } catch let error as LoadError {
            throw error
        } catch {
            throw LoadError.invalid(String(describing: error))
        }
    }

    /// The programs in picker order: the main programs, then the fault programs, each in natural id order.
    var programList: [BenchProgram] {
        programs.programs.values.sorted { a, b in
            (a.fault == nil) != (b.fault == nil)
                ? a.fault == nil
                : a.id.rawValue.localizedStandardCompare(b.id.rawValue) == .orderedAscending
        }
    }

    /// The qualification tasks in their fixed order (docs/DESIGN.md section 16).
    static let qualificationTasks: [TaskID] = ["qual-reference", "qual-hold", "qual-line"]
}

/// Facts about this Mac and this process that a session records.
struct MacFacts: Sendable {
    /// The `hw.model` identifier, for example `Mac16,1`.
    var model: String
    var os: String
    var nativeScale: Double
    var app: AppInfo

    @MainActor static func current() -> MacFacts {
        let info = Bundle.main.infoDictionary ?? [:]
        let version = ProcessInfo.processInfo.operatingSystemVersion
        let patch = version.patchVersion > 0 ? ".\(version.patchVersion)" : ""
        return MacFacts(
            model: sysctl("hw.model") ?? "unknown",
            os: "macOS \(version.majorVersion).\(version.minorVersion)\(patch)",
            nativeScale: Double(NSScreen.main?.backingScaleFactor ?? 2),
            app: AppInfo(version: info["CFBundleShortVersionString"] as? String ?? "0",
                         build: info["CFBundleVersion"] as? String ?? "0",
                         sourceRevision: sourceRevision(info["SCRIPTSourceRevision"]))
        )
    }

    /// The revision that `./check` stamps into Info.plist. A build without it records `unknown`.
    static func sourceRevision(_ value: Any?) -> String {
        guard let text = value as? String, !text.isEmpty else { return "unknown" }
        return text
    }

    private static func sysctl(_ name: String) -> String? {
        var size = 0
        guard sysctlbyname(name, nil, &size, nil, 0) == 0, size > 0 else { return nil }
        var buffer = [CChar](repeating: 0, count: size)
        guard sysctlbyname(name, &buffer, &size, nil, 0) == 0 else { return nil }
        return String(decoding: buffer.prefix { $0 != 0 }.map(UInt8.init), as: UTF8.self)
    }
}

/// The boot and the clocks of this launch. Samples and hover lines carry `NSEvent.timestamp`, app events this uptime.
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
}

/// The last qualification session of each tablet asset and pen, in one file of the app's folder. It holds no
/// participant data.
struct QualificationRegistry: Sendable {
    let file: URL

    private static func slot(asset: Code, pen: TabletPen) -> String { "\(asset.rawValue)|\(pen.rawValue)" }

    private func all() -> [String: [String]] {
        guard let data = try? Data(contentsOf: file),
              let all = try? JSONSerialization.jsonObject(with: data) as? [String: [String]] else { return [:] }
        return all
    }

    func last(asset: Code, pen: TabletPen) -> QualificationRef? {
        guard let pair = all()[Self.slot(asset: asset, pen: pen)], pair.count == 2,
              let uuid = UUID(uuidString: pair[0]), let at = try? Date(pair[1], strategy: .iso8601)
        else { return nil }
        return QualificationRef(sessionID: SessionID(uuid), at: WallTime(at))
    }

    /// Records a closed qualification session of a tablet. A session without a tablet changes nothing.
    func record(_ opened: SessionOpened) throws {
        guard let tablet = opened.tablet else { return }
        var all = all()
        all[Self.slot(asset: tablet.asset, pen: tablet.pen)] = [opened.sessionID.description, opened.openedAt.text]
        try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        try JSONSerialization.data(withJSONObject: all, options: [.sortedKeys]).write(to: file, options: .atomic)
    }
}

/// The key of the export index, in a file that only this user can read. The app has no Keychain entitlement,
/// because a local build has no team, and a qualification export indexes the code `QUAL` only.
struct FileDeviceSecret: DeviceSecret {
    let file: URL

    func key() throws -> SymmetricKey {
        if let data = try? Data(contentsOf: file), data.count == 32 { return SymmetricKey(data: data) }
        try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        let key = SymmetricKey(size: .bits256)
        let descriptor = open(file.path, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0o600)
        if descriptor < 0 {
            // Another caller created the file first, and the index must use one key only.
            guard errno == EEXIST, let data = try? Data(contentsOf: file), data.count == 32 else {
                throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno))
            }
            return SymmetricKey(data: data)
        }
        defer { close(descriptor) }
        let written = key.withUnsafeBytes { write(descriptor, $0.baseAddress, $0.count) }
        guard written == 32, fsync(descriptor) == 0 else { throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno)) }
        return key
    }
}

enum LaunchMode {
    /// A session opens before the clock check, so the run screen can be seen on a Mac without a tablet.
    static var skipsClockCheck: Bool {
        #if DEBUG
        ProcessInfo.processInfo.arguments.contains("-ScriptBenchWithoutTablet")
        #else
        false
        #endif
    }
}
