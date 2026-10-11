import Foundation
import Darwin

/// PTY（伪终端）封装：spawn 子进程并通过 PTY 通信。
///
/// 当前实现：使用 `Process`（Foundation）+ 标准 pipe。
/// 这是简化版，不提供真 PTY 行为（hermes CLI 可能不识别 isatty），
/// 但 Swift 不支持 fork()，需要真 PTY 时改用 `posix_spawn` + 直接 Darwin syscall。
///
/// TODO: 接入 `posix_spawn` 实现真 PTY 支持（hermes 颜色 / 进度条等需要 tty）
public actor PTYTerminal {
    public struct Config: Sendable {
        public var command: String
        public var arguments: [String]
        public var environment: [String: String]
        public var cols: Int
        public var rows: Int

        public init(
            command: String,
            arguments: [String] = [],
            environment: [String: String] = [:],
            cols: Int = 80,
            rows: Int = 24
        ) {
            self.command = command
            self.arguments = arguments
            self.environment = environment
            self.cols = cols
            self.rows = rows
        }
    }

    private(set) public var process: Process?
    private(set) public var stdinPipe: Pipe?
    private(set) public var stdoutPipe: Pipe?
    private(set) public var stderrPipe: Pipe?
    public var config: Config
    private var readSource: DispatchSourceRead?
    private let readQueue = DispatchQueue(label: "pty.read", qos: .utility)

    public init(config: Config) {
        self.config = config
    }

    /// 启动子进程。
    public func spawn() throws {
        guard process == nil else { throw PTYError.alreadyRunning }

        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: config.command)
        proc.arguments = config.arguments
        var env = config.environment
        env["TERM"] = env["TERM"] ?? "xterm-256color"
        env["COLORTERM"] = env["COLORTERM"] ?? "truecolor"
        proc.environment = env

        let stdinPipe = Pipe()
        let stdoutPipe = Pipe()
        let stderrPipe = Pipe()
        proc.standardInput = stdinPipe
        proc.standardOutput = stdoutPipe
        proc.standardError = stderrPipe

        try proc.run()
        self.process = proc
        self.stdinPipe = stdinPipe
        self.stdoutPipe = stdoutPipe
        self.stderrPipe = stderrPipe
    }

    /// 写数据到子进程 stdin。
    public func write(_ data: Data) throws {
        guard let pipe = stdinPipe else { throw PTYError.notRunning }
        try pipe.fileHandleForWriting.write(contentsOf: data)
    }

    public func write(_ string: String) throws {
        try write(Data(string.utf8))
    }

    /// 启动后台读取 stdout，收到数据后调 onChunk。
    /// stderr 单独合并到 stdout。
    public func startReading(onChunk: @escaping @Sendable (Data) -> Void) {
        stopReading()
        guard let pipe = stdoutPipe else { return }
        let fd = pipe.fileHandleForReading.fileDescriptor
        let src = DispatchSource.makeReadSource(fileDescriptor: fd, queue: readQueue)
        src.setEventHandler {
            var buf = [UInt8](repeating: 0, count: 4096)
            let n = buf.withUnsafeMutableBufferPointer { ptr in
                Darwin.read(fd, ptr.baseAddress, ptr.count)
            }
            if n > 0 {
                let data = Data(bytes: buf, count: n)
                onChunk(data)
            }
        }
        src.resume()
        readSource = src
    }

    public func stopReading() {
        readSource?.cancel()
        readSource = nil
    }

    /// 终止子进程并关闭管道。
    public func terminate() {
        stopReading()
        process?.terminate()
        try? stdinPipe?.fileHandleForWriting.close()
        try? stdoutPipe?.fileHandleForReading.close()
        try? stderrPipe?.fileHandleForReading.close()
    }

    /// 等待子进程退出。
    public func waitForExit() async {
        guard let proc = process else { return }
        // Process.isRunning 在 Swift Foundation 是同步；用 Task.sleep 轮询
        while proc.isRunning {
            try? await Task.sleep(nanoseconds: 50_000_000)
        }
    }
}

public enum PTYError: Error, LocalizedError, Sendable {
    case alreadyRunning
    case notRunning
    case spawnFailed(reason: String)

    public var errorDescription: String? {
        switch self {
        case .alreadyRunning: return "PTY 已在运行"
        case .notRunning: return "PTY 未运行"
        case .spawnFailed(let reason): return "spawn 失败：\(reason)"
        }
    }
}
