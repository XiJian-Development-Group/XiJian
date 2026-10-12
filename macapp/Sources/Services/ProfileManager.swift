import Foundation

/// Hermes profile 管理器：枚举 ~/.hermes/profiles/ 下的 profile、切换 active profile、创建/删除。
public actor ProfileManager {
    /// 单个 profile（默认 profile 是 "~/.hermes" 本身，named profile 是 "~/.hermes/profiles/<name>/"）
    public struct Profile: Sendable, Identifiable, Hashable {
        public let id: String
        public let path: URL
        public var isDefault: Bool { id == "default" }
        public var isActive: Bool = false
    }

    public static let shared = ProfileManager()

    /// Hermes 安装根目录（默认 ~/.hermes）
    private let hermesHome: URL

    public init(hermesHome: URL? = nil) {
        if let provided = hermesHome {
            self.hermesHome = provided
        } else {
            self.hermesHome = URL(fileURLWithPath: ("~/.hermes" as NSString).expandingTildeInPath)
        }
    }

    /// 列出所有 profile。返回数组第一个总是 default（如果 home 存在）。
    public func listProfiles() throws -> [Profile] {
        let fm = FileManager.default
        var profiles: [Profile] = []

        if fm.fileExists(atPath: hermesHome.path) {
            profiles.append(Profile(id: "default", path: hermesHome))
        }

        let profilesRoot = hermesHome.appendingPathComponent("profiles", isDirectory: true)
        if fm.fileExists(atPath: profilesRoot.path) {
            let contents = try fm.contentsOfDirectory(atPath: profilesRoot.path)
            for name in contents.sorted() {
                // 跳过隐藏文件
                if name.hasPrefix(".") { continue }
                let profilePath = profilesRoot.appendingPathComponent(name, isDirectory: true)
                var isDir: ObjCBool = false
                guard fm.fileExists(atPath: profilePath.path, isDirectory: &isDir), isDir.boolValue else { continue }
                profiles.append(Profile(id: name, path: profilePath))
            }
        }

        let active = (try? activeProfile()) ?? "default"
        return profiles.map { p in
            var copy = p
            copy.isActive = (p.id == active)
            return copy
        }
    }

    /// 读取 `~/.hermes/active_profile` 文件内容（"default" = 默认 profile）。
    public func activeProfile() throws -> String {
        let path = hermesHome.appendingPathComponent("active_profile")
        guard FileManager.default.fileExists(atPath: path.path) else {
            return "default"
        }
        let content = try String(contentsOf: path, encoding: .utf8)
        let trimmed = content.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? "default" : trimmed
    }

    /// 设置 active profile（写入 `~/.hermes/active_profile`）。
    /// "default" 是清除文件；其他名字是原子写入。
    public func setActiveProfile(_ name: String) throws {
        let path = hermesHome.appendingPathComponent("active_profile")
        if name == "default" {
            try? FileManager.default.removeItem(at: path)
            return
        }
        // 校验 profile 存在
        let profilesRoot = hermesHome.appendingPathComponent("profiles", isDirectory: true)
        let candidate = profilesRoot.appendingPathComponent(name, isDirectory: true)
        guard FileManager.default.fileExists(atPath: candidate.path) else {
            throw ProfileError.notFound(name: name)
        }
        try "".write(to: path, atomically: false, encoding: .utf8)
        let tmp = path.deletingLastPathComponent()
            .appendingPathComponent("active_profile.tmp")
        try name.write(to: tmp, atomically: false, encoding: .utf8)
        try FileManager.default.removeItem(at: path)
        try FileManager.default.moveItem(at: tmp, to: path)
    }

    /// 创建一个新的 named profile。
    public func createProfile(name: String) throws -> Profile {
        // 校验 ID 格式（与小写字母数字 + 连字符下划线，长度 1-64 兼容）
        let validChars = CharacterSet.lowercaseLetters
            .union(.uppercaseLetters)
            .union(.decimalDigits)
            .union(CharacterSet(charactersIn: "-_"))
        if name.isEmpty || name.count > 64 || name.unicodeScalars.contains(where: { !validChars.contains($0) }) {
            throw ProfileError.invalidName(name: name)
        }
        let profilesRoot = hermesHome.appendingPathComponent("profiles", isDirectory: true)
        try FileManager.default.createDirectory(at: profilesRoot, withIntermediateDirectories: true)
        let profileDir = profilesRoot.appendingPathComponent(name, isDirectory: true)
        if FileManager.default.fileExists(atPath: profileDir.path) {
            throw ProfileError.alreadyExists(name: name)
        }
        try FileManager.default.createDirectory(at: profileDir, withIntermediateDirectories: true)
        // 创建必要的子目录（参考 hermes profile 布局）
        for sub in ["memories", "sessions", "skills", "logs", "plans", "workspace", "cron"] {
            try FileManager.default.createDirectory(
                at: profileDir.appendingPathComponent(sub, isDirectory: true),
                withIntermediateDirectories: true
            )
        }
        return Profile(id: name, path: profileDir)
    }

    /// 删除一个 named profile。default profile 不可删。
    public func deleteProfile(_ name: String) throws {
        guard name != "default" else { throw ProfileError.cannotDeleteDefault }
        let profilesRoot = hermesHome.appendingPathComponent("profiles", isDirectory: true)
        let profileDir = profilesRoot.appendingPathComponent(name, isDirectory: true)
        guard FileManager.default.fileExists(atPath: profileDir.path) else {
            throw ProfileError.notFound(name: name)
        }
        // 如果正在 active，先清掉
        if (try? activeProfile()) == name {
            try? FileManager.default.removeItem(at: hermesHome.appendingPathComponent("active_profile"))
        }
        try FileManager.default.removeItem(at: profileDir)
    }
}

public enum ProfileError: Error, LocalizedError, Sendable {
    case notFound(name: String)
    case alreadyExists(name: String)
    case invalidName(name: String)
    case cannotDeleteDefault

    public var errorDescription: String? {
        switch self {
        case .notFound(let name): return "Profile 不存在：\(name)"
        case .alreadyExists(let name): return "Profile 已存在：\(name)"
        case .invalidName(let name): return "Profile 名无效：\(name)（仅允许字母数字 -_，长度 1-64）"
        case .cannotDeleteDefault: return "default profile 不可删除"
        }
    }
}
