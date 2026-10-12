import Foundation

/// SOUL.md 文档模型：按 `# 标题` 切分章节（无嵌套）。
/// 解析 + 反序列化保持内容完全保留。
public struct SoulDocument: Sendable, Equatable {
    public struct Section: Sendable, Identifiable, Equatable {
        public let id: UUID
        public var title: String
        public var content: String

        public init(id: UUID = UUID(), title: String, content: String) {
            self.id = id
            self.title = title
            self.content = content
        }
    }

    public var sections: [Section]
    public var rawMarkdown: String

    public init(sections: [Section] = [], rawMarkdown: String = "") {
        self.sections = sections
        self.rawMarkdown = rawMarkdown
    }

    /// 从 markdown 解析。每行以 `# ` 开头是新章节标题；其他行是上一章节内容。
    /// 如果文档无标题，创建一个 `(untitled)` 章节包含全部内容。
    public static func parse(_ markdown: String) -> SoulDocument {
        var sections: [Section] = []
        var current: Section? = nil
        var contentLines: [String] = []

        func flush() {
            if let c = current {
                var copy = c
                copy.content = contentLines.joined(separator: "\n")
                    .trimmingCharacters(in: .whitespacesAndNewlines)
                sections.append(copy)
            } else if !contentLines.isEmpty {
                let body = contentLines.joined(separator: "\n")
                    .trimmingCharacters(in: .whitespacesAndNewlines)
                if !body.isEmpty {
                    sections.append(Section(title: "(untitled)", content: body))
                }
            }
            contentLines = []
        }

        for line in markdown.split(separator: "\n", omittingEmptySubsequences: false) {
            if line.hasPrefix("# ") && !line.hasPrefix("#  ") {
                flush()
                let title = String(line.dropFirst(2))
                    .trimmingCharacters(in: .whitespaces)
                current = Section(title: title, content: "")
            } else if current != nil {
                contentLines.append(String(line))
            } else {
                contentLines.append(String(line))
            }
        }
        flush()

        return SoulDocument(sections: sections, rawMarkdown: markdown)
    }

    /// 反序列化为 markdown。
    public func toMarkdown() -> String {
        var lines: [String] = []
        for section in sections {
            lines.append("# \(section.title)")
            if !section.content.isEmpty {
                lines.append("")
                lines.append(section.content)
            }
            lines.append("")
        }
        return lines.joined(separator: "\n")
    }

    /// 加载 `~/.hermes/SOUL.md`（default profile）或 `~/.hermes/profiles/<name>/SOUL.md`。
    public static func load(from url: URL) throws -> SoulDocument {
        let fm = FileManager.default
        guard fm.fileExists(atPath: url.path) else {
            // 不存在：返回空文档
            return SoulDocument()
        }
        let content = try String(contentsOf: url, encoding: .utf8)
        return parse(content)
    }

    /// 原子保存到 url。
    public func save(to url: URL) throws {
        let markdown = toMarkdown()
        let tmp = url.deletingLastPathComponent()
            .appendingPathComponent("SOUL.md.tmp")
        try markdown.write(to: tmp, atomically: false, encoding: .utf8)
        if FileManager.default.fileExists(atPath: url.path) {
            try FileManager.default.removeItem(at: url)
        }
        try FileManager.default.moveItem(at: tmp, to: url)
    }
}
