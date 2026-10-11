import XCTest
@testable import XiJianManagerKit

final class HermesInstallerTests: XCTestCase {

    // MARK: detect

    func test_detect_returnsNotInstalled_whenNoHermesInPath() async {
        // 用不存在的目录作为 searchPaths
        let installer = HermesInstaller(searchPaths: ["/tmp/does-not-exist-\(UUID().uuidString)"])
        let status = await installer.detect()
        XCTAssertFalse(status.installed)
        XCTAssertNil(status.path)
        XCTAssertNil(status.version)
    }

    func test_detect_returnsInstalled_whenFakeHermesInPath() async throws {
        let tmpDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("hermes-test-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: tmpDir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: tmpDir) }

        let fakeHermes = tmpDir.appendingPathComponent("hermes")
        let script = """
        #!/bin/sh
        if [ "$1" = "--version" ]; then
          echo "hermes 0.42.0"
          exit 0
        fi
        exit 0
        """
        try script.write(to: fakeHermes, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o755],
            ofItemAtPath: fakeHermes.path
        )

        let installer = HermesInstaller(searchPaths: [tmpDir.path])
        let status = await installer.detect()
        XCTAssertTrue(status.installed)
        XCTAssertEqual(status.path, fakeHermes)
        XCTAssertEqual(status.version, "hermes 0.42.0")
    }

    // MARK: backup / restore

    func test_backupAndRestore_roundtrip() async throws {
        let srcDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("hermes-src-\(UUID().uuidString)", isDirectory: true)
        let destDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("hermes-restore-\(UUID().uuidString)", isDirectory: true)
        defer {
            try? FileManager.default.removeItem(at: srcDir)
            try? FileManager.default.removeItem(at: destDir)
        }
        try FileManager.default.createDirectory(at: srcDir, withIntermediateDirectories: true)

        // 准备 fixture 文件
        let fixture = srcDir.appendingPathComponent("marker.txt")
        try "hello".write(to: fixture, atomically: true, encoding: .utf8)

        // 备份到临时目录
        let backupDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("hermes-backups-\(UUID().uuidString)", isDirectory: true)
        let installer = HermesInstaller(searchPaths: ["/tmp/none"])
        try await installer.backup(installPath: srcDir, to: backupDir)

        // 找到生成的 tarball
        let archives = try FileManager.default.contentsOfDirectory(at: backupDir, includingPropertiesForKeys: nil)
        let tarballs = archives.filter { $0.pathExtension == "gz" }
        XCTAssertEqual(tarballs.count, 1, "应当恰好生成一个备份 tarball")
        let archive = tarballs.first!

        // 恢复到 destDir
        try await installer.restore(from: archive, to: destDir)
        let restored = destDir.appendingPathComponent("marker.txt")
        XCTAssertTrue(FileManager.default.fileExists(atPath: restored.path),
                      "恢复后 marker.txt 应存在")
        let content = try String(contentsOf: restored, encoding: .utf8)
        XCTAssertEqual(content, "hello")
    }

    // MARK: error mapping

    func test_installError_alreadyInstalled_describesPath() {
        let path = URL(fileURLWithPath: "/opt/hermes")
        let err = InstallError.alreadyInstalled(path: path)
        XCTAssertNotNil(err.errorDescription)
        XCTAssertTrue(err.errorDescription!.contains("/opt/hermes"))
    }

    func test_installError_isSendable() {
        // 编译时验证：InstallError 是 Sendable
        let err: any Error & Sendable = InstallError.notInstalled
        let _: Sendable = err
        XCTAssertNotNil(err)
    }
}
