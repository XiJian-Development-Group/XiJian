import Foundation

enum CoreState: Equatable {
    case stopped
    case extracting
    case starting
    case running(port: Int)
    case error(String)

    static func == (lhs: CoreState, rhs: CoreState) -> Bool {
        switch (lhs, rhs) {
        case (.stopped, .stopped), (.extracting, .extracting), (.starting, .starting):
            return true
        case (.running(let a), .running(let b)):
            return a == b
        case (.error(let a), .error(let b)):
            return a == b
        default:
            return false
        }
    }
}

@Observable
final class CoreManager {
    var state: CoreState = .stopped
    var port: Int { customPort ?? resolvedPort }
    var corePath: String = ""
    var pythonPath: String = ""
    var customPort: Int?
    var customServerURL: String?

    private var resolvedPort: Int = 5000
    private var process: Process?
    private var portCheckTimer: Timer?

    private let supportDir: String
    private let coreDir: String
    private let coreExecutableName = "xijian-core"

    init() {
        let appSupport = NSSearchPathForDirectoriesInDomains(.applicationSupportDirectory, .userDomainMask, true).first!
        supportDir = "\(appSupport)/XiJian/Main"
        coreDir = "\(supportDir)/CoreFull"

        corePath = "\(coreDir)/xijian-core/xijian-core"
        pythonPath = "/usr/bin/python3"
    }

    func start() async {
        if let url = customServerURL, let _ = URL(string: url) {
            state = .running(port: port)
            return
        }

        state = .starting

        do {
            if !FileManager.default.fileExists(atPath: "\(coreDir)/xijian-core") {
                try await extractCore()
            }

            let execPath = "\(coreDir)/xijian-core/xijian-core"
            guard FileManager.default.fileExists(atPath: execPath) else {
                state = .error("核心组件未找到: \(execPath)")
                return
            }

            try launchCore(at: execPath)

            try await Task.sleep(nanoseconds: 2_000_000_000)
            resolvedPort = try discoverPort() ?? 5000
            state = .running(port: port)
        } catch {
            state = .error("启动失败: \(error.localizedDescription)")
        }
    }

    func stop() {
        process?.terminate()
        process = nil
        portCheckTimer?.invalidate()
        portCheckTimer = nil
        state = .stopped
    }

    func resetCore() throws {
        stop()
        if FileManager.default.fileExists(atPath: coreDir) {
            try FileManager.default.removeItem(atPath: coreDir)
        }
    }

    // MARK: - Private

    private func extractCore() async throws {
        state = .extracting

        let fm = FileManager.default
        try fm.createDirectory(atPath: coreDir, withIntermediateDirectories: true)

        guard let archivePath = Bundle.main.path(forResource: "xijian-core", ofType: "7z") else {
            throw CoreError.assetNotFound("xijian-core.7z")
        }

        guard let py7zrPath = Bundle.main.path(forResource: "py7zr_bundle", ofType: nil) else {
            throw CoreError.assetNotFound("py7zr_bundle")
        }

        let script = """
        import sys, os
        sys.path.insert(0, '\(py7zrPath)')
        os.chdir('\(coreDir)')
        import py7zr
        with py7zr.SevenZipFile('\(archivePath)', mode='r') as z:
            z.extractall('.')
        print('extraction complete')
        """

        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: pythonPath)
        proc.arguments = ["-c", script]

        let pipe = Pipe()
        proc.standardOutput = pipe

        try proc.run()
        proc.waitUntilExit()

        guard proc.terminationStatus == 0 else {
            throw CoreError.extractionFailed("退出码: \(proc.terminationStatus)")
        }

        let execPath = "\(coreDir)/xijian-core/xijian-core"
        try fm.setAttributes([.posixPermissions: 0o755], ofItemAtPath: execPath)
    }

    private func launchCore(at path: String) throws {
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: path)
        proc.currentDirectoryURL = URL(fileURLWithPath: "\(coreDir)/xijian-core")

        let configToml = "\(coreDir)/xijian-core/config.toml"
        if FileManager.default.fileExists(atPath: configToml) {
            proc.arguments = ["--config", configToml]
        }

        proc.terminationHandler = { [weak self] p in
            DispatchQueue.main.async {
                if p.terminationStatus != 0 && p.terminationStatus != 15 {
                    self?.state = .error("进程异常退出 (code: \(p.terminationStatus))")
                } else {
                    self?.state = .stopped
                }
                self?.process = nil
            }
        }

        try proc.run()
        process = proc
    }

    private func discoverPort() -> Int? {
        let fm = FileManager.default
        guard let tmpDir = NSTemporaryDirectory().addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) else {
            return nil
        }
        let tmpURL = URL(fileURLWithPath: tmpDir).deletingLastPathComponent()

        do {
            let files = try fm.contentsOfDirectory(atPath: "/tmp")
            for file in files where file.hasPrefix("xijian-") && file.hasSuffix(".port") {
                let path = "/tmp/\(file)"
                let content = try String(contentsOfFile: path, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines)
                if let port = Int(content) {
                    return port
                }
            }
        } catch {}

        return nil
    }

    func verifyPythonVersion(at path: String) -> Bool {
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: path)
        proc.arguments = ["--version"]

        let pipe = Pipe()
        proc.standardOutput = pipe

        guard (try? proc.run()) != nil else { return false }
        proc.waitUntilExit()

        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        let versionStr = String(data: data, encoding: .utf8) ?? ""
        let parts = versionStr.split(separator: " ").last?.split(separator: ".").map(String.init) ?? []
        guard parts.count >= 2, let major = Int(parts[0]), let minor = Int(parts[1]) else {
            return false
        }
        return major > 3 || (major == 3 && minor >= 12)
    }
}

enum CoreError: LocalizedError {
    case assetNotFound(String)
    case extractionFailed(String)

    var errorDescription: String? {
        switch self {
        case .assetNotFound(let name):
            return "资源文件未找到: \(name)"
        case .extractionFailed(let reason):
            return "解压失败: \(reason)"
        }
    }
}
