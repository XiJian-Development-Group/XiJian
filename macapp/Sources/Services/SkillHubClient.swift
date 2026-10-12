import Foundation

/// SkillHub 客户端：列出已安装 skill + 从 git URL 安装新 skill。
public actor SkillHubClient {
    public struct Skill: Sendable, Identifiable, Hashable {
        public let id: String  // skill 名（目录名）
        public let path: URL
        public var description: String
        public var version: String
        public var installed: Bool = true

        public init(id: String, path: URL, description: String = "", version: String = "") {
            self.id = id
            self.path = path
            self.description = description
            self.version = version
        }
    }

    public static let shared = SkillHubClient()

    private let skillsRoot: URL

    public init(skillsRoot: URL? = nil) {
        if let p = skillsRoot {
            self.skillsRoot = p
        } else {
            self.skillsRoot = URL(fileURLWithPath: ("~/.hermes/skills" as NSString).expandingTildeInPath)
        }
    }

    /// 列出已安装 skill。读取每个 skill 目录的 SKILL.md frontmatter（`name` / `description` / `version`）。
    public func listInstalled() throws -> [Skill] {
        let fm = FileManager.default
        try fm.createDirectory(at: skillsRoot, withIntermediateDirectories: true)
        let contents = try fm.contentsOfDirectory(at: skillsRoot, includingPropertiesForKeys: [.isDirectoryKey])
        var skills: [Skill] = []
        for url in contents {
            guard let isDir = (try? url.resourceValues(forKeys: [.isDirectoryKey]).isDirectory), isDir else { continue }
            // 跳过隐藏目录
            if url.lastPathComponent.hasPrefix(".") { continue }
            let skillMd = url.appendingPathComponent("SKILL.md")
            var description = ""
            var version = ""
            if fm.fileExists(atPath: skillMd.path) {
                let content = (try? String(contentsOf: skillMd, encoding: .utf8)) ?? ""
                let meta = parseFrontmatter(content)
                description = meta["description"] ?? ""
                version = meta["version"] ?? ""
            }
            skills.append(Skill(
                id: url.lastPathComponent,
                path: url,
                description: description,
                version: version
            ))
        }
        return skills.sorted { $0.id < $1.id }
    }

    /// 安装 skill：从 git URL clone 到 skillsRoot/<name>。
    public func install(fromGitURL urlString: String) async throws -> Skill {
        // 从 URL 末尾提取目录名
        let name = urlString.split(separator: "/").last
            .map { String($0).replacingOccurrences(of: ".git", with: "") } ?? "unknown-skill"
        let target = skillsRoot.appendingPathComponent(name, isDirectory: true)

        // 复用 git 命令
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/git")
        process.arguments = ["clone", urlString, target.path]
        let errPipe = Pipe()
        process.standardError = errPipe
        try process.run()
        process.waitUntilExit()
        if process.terminationStatus != 0 {
            let err = String(data: errPipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
            throw SkillError.cloneFailed(reason: "git clone 退出码 \(process.terminationStatus): \(err)")
        }

        // 重新读取 SKILL.md 元数据
        let skillMd = target.appendingPathComponent("SKILL.md")
        var description = ""
        var version = ""
        if FileManager.default.fileExists(atPath: skillMd.path) {
            let content = (try? String(contentsOf: skillMd, encoding: .utf8)) ?? ""
            let meta = parseFrontmatter(content)
            description = meta["description"] ?? ""
            version = meta["version"] ?? ""
        }
        return Skill(id: name, path: target, description: description, version: version)
    }

    /// 卸载 skill。
    public func uninstall(name: String) throws {
        let target = skillsRoot.appendingPathComponent(name, isDirectory: true)
        guard FileManager.default.fileExists(atPath: target.path) else {
            throw SkillError.notFound(name: name)
        }
        try FileManager.default.removeItem(at: target)
    }

    /// 解析简单 YAML frontmatter（`---\nkey: value\n---`）。
    /// 仅支持 `key: value` 单行格式。
    private func parseFrontmatter(_ content: String) -> [String: String] {
        var meta: [String: String] = [:]
        let lines = content.split(separator: "\n", omittingEmptySubsequences: false)
        var inFront = false
        var sawEnd = false
        for line in lines {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if trimmed == "---" {
                if !inFront {
                    inFront = true
                    continue
                } else {
                    sawEnd = true
                    break
                }
            }
            if inFront, let colonIdx = trimmed.firstIndex(of: ":") {
                let key = trimmed[..<colonIdx].trimmingCharacters(in: .whitespaces)
                let value = trimmed[trimmed.index(after: colonIdx)...].trimmingCharacters(in: .whitespaces)
                meta[key] = value
            }
        }
        _ = sawEnd
        return meta
    }
}

public enum SkillError: Error, LocalizedError, Sendable {
    case notFound(name: String)
    case cloneFailed(reason: String)

    public var errorDescription: String? {
        switch self {
        case .notFound(let name): return "Skill 不存在：\(name)"
        case .cloneFailed(let reason): return "克隆失败：\(reason)"
        }
    }
}
