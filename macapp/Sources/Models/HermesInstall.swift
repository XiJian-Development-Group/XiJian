import Foundation

/// Hermes 安装选项
public struct HermesInstallOptions: Sendable {
    public var installPath: URL
    public var noCAVerify: Bool
    public var mirrorCN: Bool
    public var backupBeforeInstall: Bool
    public var backupDirectory: URL
    public var githubOwner: String
    public var githubRepo: String

    public init(
        installPath: URL = URL(fileURLWithPath: ("~/.hermes" as NSString).expandingTildeInPath),
        noCAVerify: Bool = false,
        mirrorCN: Bool = false,
        backupBeforeInstall: Bool = true,
        backupDirectory: URL = URL(fileURLWithPath: ("~/Documents/XiJianBackups" as NSString).expandingTildeInPath),
        githubOwner: String = "NousResearch",
        githubRepo: String = "hermes-agent"
    ) {
        self.installPath = installPath
        self.noCAVerify = noCAVerify
        self.mirrorCN = mirrorCN
        self.backupBeforeInstall = backupBeforeInstall
        self.backupDirectory = backupDirectory
        self.githubOwner = githubOwner
        self.githubRepo = githubRepo
    }
}
