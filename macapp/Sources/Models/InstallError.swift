import Foundation

/// 安装/卸载/备份错误
public enum InstallError: Error, LocalizedError, Sendable {
    case alreadyInstalled(path: URL)
    case notInstalled
    case downloadFailed(url: URL, statusCode: Int)
    case extractFailed(reason: String)
    case backupFailed(reason: String)
    case networkError(underlying: String)
    case githubAPIError(statusCode: Int, message: String)
    case noReleasesAvailable

    public var errorDescription: String? {
        switch self {
        case .alreadyInstalled(let path):
            return "Hermes 已安装在 \(path.path)"
        case .notInstalled:
            return "Hermes 未安装"
        case .downloadFailed(let url, let status):
            return "下载失败：\(url.lastPathComponent)（HTTP \(status)）"
        case .extractFailed(let reason):
            return "解压失败：\(reason)"
        case .backupFailed(let reason):
            return "备份失败：\(reason)"
        case .networkError(let underlying):
            return "网络错误：\(underlying)"
        case .githubAPIError(let status, let message):
            return "GitHub API 错误（\(status)）：\(message)"
        case .noReleasesAvailable:
            return "Hermes 仓库没有可用的 release"
        }
    }
}
