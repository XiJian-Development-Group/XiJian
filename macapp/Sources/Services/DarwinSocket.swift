import Foundation

/// 抢端口用的小工具。
///
/// 放在单独一个文件里，是因为 socket 那几行又长又容易写错，
/// 而且测试要能直接用它验证「这个端口确实是空的」。
enum DarwinSocket {
    /// 开一个 TCP socket。
    static func make() -> Int32 {
        socket(AF_INET, SOCK_STREAM, 0)
    }

    /// 绑到 127.0.0.1 的某个端口。端口传 0 就是让系统挑一个空端口。
    @discardableResult
    static func bindLoopback(_ handle: Int32, port: UInt16 = 0) -> Bool {
        var address = sockaddr_in()
        address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        address.sin_family = sa_family_t(AF_INET)
        address.sin_port = port.bigEndian
        address.sin_addr.s_addr = inet_addr("127.0.0.1")
        let bound = withUnsafePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { sockaddrPointer in
                bind(handle, sockaddrPointer, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        return bound == 0
    }

    /// socket 绑到的端口号。
    static func portOf(_ handle: Int32) -> Int? {
        var address = sockaddr_in()
        var length = socklen_t(MemoryLayout<sockaddr_in>.size)
        let named = withUnsafeMutablePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { sockaddrPointer in
                getsockname(handle, sockaddrPointer, &length)
            }
        }
        guard named == 0 else { return nil }
        return Int(UInt16(bigEndian: address.sin_port))
    }

    /// 这个端口是不是空着的。
    static func isFree(_ port: Int) -> Bool {
        let handle = make()
        guard handle >= 0 else { return false }
        defer { closeSocket(handle) }
        return bindLoopback(handle, port: UInt16(port))
    }

    static func closeSocket(_ handle: Int32) {
        guard handle >= 0 else { return }
        Darwin.close(handle)
    }
}

/// 跑 hermes 命令出错。
public enum HermesCommandError: Error, LocalizedError {
    case commandFailed(String)
    case timeout(Int)

    public var errorDescription: String? {
        switch self {
        case .commandFailed(let message):
            return message.isEmpty ? "Hermes 命令执行失败" : message
        case .timeout(let port):
            return "Hermes gateway 没能在端口 \(port) 上起来"
        }
    }
}
