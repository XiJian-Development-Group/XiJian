import Foundation

/// 日志 tail：异步读文件追加行，回调每行。
///
/// 实现：异步 Task 循环，每 100ms 轮询文件大小。文件被 truncate 时自动重置。
/// 不依赖 DispatchSource（xctest 环境下 timer handler 可能不触发）。
public actor LogTailer {
    private var pollTask: Task<Void, Never>?
    private var fileURL: URL?
    private var lastOffset: UInt64 = 0
    private var buffer = Data()
    private var onLine: (@Sendable (String) -> Void)?

    public init() {}

    /// 开始 tail 一个文件。
    public func start(file: URL, onLine: @escaping @Sendable (String) -> Void) async throws {
        stop()
        fileURL = file
        self.onLine = onLine
        lastOffset = 0
        buffer = Data()

        if FileManager.default.fileExists(atPath: file.path) {
            try await readExisting(from: file)
        } else {
            try await waitForFile(file)
        }

        // 启动 poll 循环（actor 自己的 Task）
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 100_000_000)
                await self?.poll()
            }
        }
    }

    public func stop() {
        pollTask?.cancel()
        pollTask = nil
        onLine = nil
    }

    private func waitForFile(_ file: URL) async throws {
        let deadline = Date().addingTimeInterval(10)
        while Date() < deadline {
            if FileManager.default.fileExists(atPath: file.path) {
                try await readExisting(from: file)
                return
            }
            try await Task.sleep(nanoseconds: 100_000_000)
        }
    }

    private func readExisting(from file: URL) async throws {
        let handle = try FileHandle(forReadingFrom: file)
        defer { try? handle.close() }
        let data = handle.readDataToEndOfFile()
        lastOffset = UInt64(data.count)
        emitLines(from: data)
    }

    private func poll() {
        guard let url = fileURL else { return }
        guard FileManager.default.fileExists(atPath: url.path) else { return }
        guard let attrs = try? FileManager.default.attributesOfItem(atPath: url.path) else { return }
        guard let nsSize = attrs[.size] as? NSNumber else { return }
        let size = nsSize.uint64Value
        if size < lastOffset {
            lastOffset = 0
            buffer = Data()
        }
        if size > lastOffset {
            try? readRange(url: url, from: lastOffset, to: size)
            lastOffset = size
        }
    }

    private func readRange(url: URL, from start: UInt64, to end: UInt64) throws {
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }
        try handle.seek(toOffset: start)
        let length = Int(end - start)
        let data = handle.readData(ofLength: length)
        emitLines(from: data)
    }

    private func emitLines(from data: Data) {
        buffer.append(data)
        while let nl = buffer.firstIndex(of: 0x0A) {
            let lineData = buffer[..<nl]
            buffer.removeSubrange(buffer.startIndex...nl)
            if let line = String(data: lineData, encoding: .utf8) {
                let trim = line.trimmingCharacters(in: .whitespacesAndNewlines)
                if !trim.isEmpty {
                    onLine?(trim)
                }
            }
        }
    }
}

public enum LogTailerError: Error, LocalizedError, Sendable {
    case openFailed(path: String)

    public var errorDescription: String? {
        switch self {
        case .openFailed(let path): return "打开日志失败：\(path)"
        }
    }
}
