import Foundation
import Security

/// 凭据存储协议（S7）：Keychain 读写，测试可注入内存实现。
/// 凭据一律走 Keychain，UserDefaults 只存「已配置」标记，不留明文。
protocol KeychainStoring {
    func save(_ value: String, forKey key: String) -> Bool
    func load(forKey key: String) -> String?
    @discardableResult func delete(forKey key: String) -> Bool
}

/// 系统 Keychain 实现（kSecClassGenericPassword）。
/// Service 按环境区分：bundle id + 环境后缀，避免开发/生产串用。
final class SystemKeychainStore: KeychainStoring {
    /// 环境后缀（默认空 = 生产；DEBUG 构建追加 "-dev"，防止开发数据污染）
    private var service: String {
        var base = "com.skyc8266.xijian.macapp"
        #if DEBUG
        base += "-dev"
        #endif
        return base
    }

    func save(_ value: String, forKey key: String) -> Bool {
        guard !value.isEmpty else { return delete(forKey: key) }
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key,
        ]
        let attributes: [String: Any] = [
            kSecValueData as String: Data(value.utf8),
        ]
        // 先尝试更新，不存在则新增
        let status = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if status == errSecItemNotFound {
            var add = query
            add[kSecValueData as String] = Data(value.utf8)
            let addStatus = SecItemAdd(add as CFDictionary, nil)
            return addStatus == errSecSuccess
        }
        return status == errSecSuccess
    }

    func load(forKey key: String) -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var result: AnyObject?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        guard status == errSecSuccess, let data = result as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    func delete(forKey key: String) -> Bool {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key,
        ]
        let status = SecItemDelete(query as CFDictionary)
        // 条目不存在视为删除成功（幂等）
        return status == errSecSuccess || status == errSecItemNotFound
    }
}

/// 测试用内存实现（不触碰真实 Keychain）
final class InMemoryKeychainStore: KeychainStoring {
    private var storage: [String: String] = [:]

    func save(_ value: String, forKey key: String) -> Bool {
        storage[key] = value
        return true
    }

    func load(forKey key: String) -> String? {
        storage[key]
    }

    func delete(forKey key: String) -> Bool {
        storage.removeValue(forKey: key)
        return true
    }
}

/// 全局入口：生产用系统 Keychain，测试可替换为内存实现。
enum KeychainStore {
    static var shared: KeychainStoring = SystemKeychainStore()
}
