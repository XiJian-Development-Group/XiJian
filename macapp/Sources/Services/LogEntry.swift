import Foundation
import SwiftUI

// MARK: - 日志级别

/// 日志级别（含中文显示名与显示颜色）
enum LogLevel: Int, CaseIterable, Comparable, Identifiable {
    /// 调试
    case debug = 0
    /// 信息
    case info = 1
    /// 警告
    case warning = 2
    /// 错误
    case error = 3
    /// 严重
    case critical = 4
    /// 无法识别的级别
    case unknown = 5

    var id: Int { rawValue }

    /// 中文显示名
    var displayName: String {
        switch self {
        case .debug: return "调试"
        case .info: return "信息"
        case .warning: return "警告"
        case .error: return "错误"
        case .critical: return "严重"
        case .unknown: return "未知"
        }
    }

    /// 显示颜色（调试=灰、信息=蓝、警告=橙、错误=红、严重=紫红、未知=次要色）
    var color: Color {
        switch self {
        case .debug: return .gray
        case .info: return .blue
        case .warning: return .orange
        case .error: return .red
        case .critical: return .purple
        case .unknown: return .secondary
        }
    }

    /// 从 Core 日志级别文本解析（大小写不敏感，容忍 WARN/CRIT 等缩写）
    static func parse(_ text: String) -> LogLevel {
        switch text.trimmingCharacters(in: .whitespacesAndNewlines).uppercased() {
        case "DEBUG": return .debug
        case "INFO": return .info
        case "WARNING", "WARN": return .warning
        case "ERROR": return .error
        case "CRITICAL", "CRIT": return .critical
        default: return .unknown
        }
    }

    static func < (lhs: LogLevel, rhs: LogLevel) -> Bool {
        lhs.rawValue < rhs.rawValue
    }
}

// MARK: - 日志条目

/// 单条日志（级别 + 可选时间戳 + 消息 + 来源）
struct LogEntry: Identifiable, Equatable {
    /// 日志来源
    enum Source: Equatable {
        /// App 捕获的 Core 进程输出（appendLog 写入的环形缓冲）
        case appProcess
        /// Core 日志文件（logs/xijian-api.log）
        case coreFile

        var displayName: String {
            switch self {
            case .appProcess: return "进程输出"
            case .coreFile: return "日志文件"
            }
        }
    }

    let id: UUID
    let level: LogLevel
    let timestamp: Date?
    let message: String
    let source: Source

    init(level: LogLevel, timestamp: Date?, message: String, source: Source, id: UUID = UUID()) {
        self.id = id
        self.level = level
        self.timestamp = timestamp
        self.message = message
        self.source = source
    }

    /// 是否匹配级别筛选。
    /// - filter 为 nil 表示全部；
    /// - 其余按"最低级别"语义：显示该级别及更严重的条目；
    /// - 无法识别的未知级别行始终显示，避免遗漏原始信息。
    func matches(levelFilter: LogLevel?) -> Bool {
        guard let filter = levelFilter else { return true }
        if level == .unknown { return true }
        return level.rawValue >= filter.rawValue
    }
}

// MARK: - 解析

extension LogEntry {
    /// Core 日志时间戳解析器（yyyy-MM-dd HH:mm:ss，本机时区）
    static let coreTimestampFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd HH:mm:ss"
        formatter.timeZone = .current
        return formatter
    }()

    /// 时间部分格式校验（避免 DateFormatter 宽松解析非法日期）
    private static let coreTimePattern = try! NSRegularExpression(
        pattern: #"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$"#
    )

    /// 解析单行 Core 日志。格式：
    /// `[xijian-api] YYYY-MM-DD HH:MM:SS LEVEL 消息`
    /// - 命中格式 → 返回对应级别条目（级别大小写不敏感，未知级别记为 .unknown）
    /// - 以 `[xijian-api]` 开头但格式不符 → 返回 .unknown 条目（时间戳为 nil）
    /// - 其它行（续行 / 裸文本）→ nil（由调用方决定合并或降级处理）
    static func parseCoreLogLine(_ line: String) -> LogEntry? {
        guard line.hasPrefix("[xijian-api]") else { return nil }
        var rest = line.trimmingCharacters(in: .whitespacesAndNewlines)
        // 去掉行首标记 [xijian-api]
        rest.removeFirst("[xijian-api]".count)
        rest = rest.trimmingCharacters(in: .whitespacesAndNewlines)

        // 提取时间部分（固定 19 字符：yyyy-MM-dd HH:mm:ss）
        let timePart = String(rest.prefix(19))
        let timeNSRange = NSRange(location: 0, length: (timePart as NSString).length)
        guard coreTimePattern.firstMatch(in: timePart, range: timeNSRange) != nil,
              let timestamp = coreTimestampFormatter.date(from: timePart),
              rest.count >= 19 else {
            return LogEntry(level: .unknown, timestamp: nil, message: line, source: .coreFile)
        }
        rest.removeFirst(19)
        rest = rest.trimmingCharacters(in: .whitespaces)

        // 提取级别（首个空白前的 token）
        guard !rest.isEmpty else {
            return LogEntry(level: .unknown, timestamp: nil, message: line, source: .coreFile)
        }
        let levelToken = rest.prefix { !$0.isWhitespace }
        let level = LogLevel.parse(String(levelToken))
        let message = rest.dropFirst(levelToken.count).trimmingCharacters(in: .whitespaces)
        return LogEntry(level: level, timestamp: timestamp, message: message, source: .coreFile)
    }

    /// 解析 App 捕获的进程输出行 → 日志条目（来源 .appProcess）。
    /// - 命中 Core 日志格式 → 按格式解析级别与时间戳；消息保留原始行文本（进程实际输出，
    ///   便于与日志文件按原始行去重）
    /// - 包含 CRITICAL/ERROR/WARNING/DEBUG 关键词 → 对应级别（大小写不敏感）
    /// - 以 [XiJian] 开头或其它行 → 信息
    static func parseAppLine(_ line: String) -> LogEntry {
        let trimmed = line.trimmingCharacters(in: .whitespacesAndNewlines)
        if let coreEntry = parseCoreLogLine(trimmed) {
            return LogEntry(
                level: coreEntry.level,
                timestamp: coreEntry.timestamp,
                message: trimmed,
                source: .appProcess
            )
        }
        let upper = trimmed.uppercased()
        let level: LogLevel
        if upper.contains("CRITICAL") {
            level = .critical
        } else if upper.contains("ERROR") {
            level = .error
        } else if upper.contains("WARNING") || upper.contains("WARN") {
            level = .warning
        } else if upper.contains("DEBUG") {
            level = .debug
        } else {
            level = .info
        }
        return LogEntry(level: level, timestamp: Date(), message: trimmed, source: .appProcess)
    }

    /// 解析 Core 日志文件原始行序列。
    /// 多行 Traceback 等续行（不以 `[xijian-api]` 开头的行）归入上一行条目
    /// （沿用上一行的级别与时间戳，保留行首缩进）；文件首行即为裸文本时按未知级别单独成条；空行忽略。
    static func parseCoreLogLines(_ lines: [String]) -> [LogEntry] {
        var entries: [LogEntry] = []
        for line in lines {
            let trimmed = line.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !trimmed.isEmpty else { continue }
            if let entry = parseCoreLogLine(trimmed) {
                entries.append(entry)
            } else if let last = entries.last {
                // 续行归入上一行：保留行首缩进（如 Traceback 的缩进），仅去掉行尾空白；
                // 重建条目以保留级别/时间戳/来源，并保持 id 不变
                var continuation = line
                while let ch = continuation.last, ch.isWhitespace || ch.isNewline {
                    continuation.removeLast()
                }
                let merged = LogEntry(
                    level: last.level,
                    timestamp: last.timestamp,
                    message: last.message + "\n" + continuation,
                    source: last.source,
                    id: last.id
                )
                entries[entries.count - 1] = merged
            } else {
                entries.append(LogEntry(level: .unknown, timestamp: nil, message: trimmed, source: .coreFile))
            }
        }
        return entries
    }
}
