import XCTest
@testable import XiJianManagerKit

final class CNNetworkAdapterTests: XCTestCase {

    // MARK: - 关掉加速

    func test_mirrorOff_returnsOriginalURL() async {
        let adapter = CNNetworkAdapter(options: CNNetworkAdapter.Options(mirrorCN: false))
        let original = URL(string: "https://github.com/foo/bar")!
        let result = await adapter.githubURL(original)
        XCTAssertEqual(result, original)
    }

    func test_mirrorOff_hasNoSources() async {
        let adapter = CNNetworkAdapter(options: CNNetworkAdapter.Options(mirrorCN: false))
        let npm = await adapter.npmRegistry()
        let pypi = await adapter.pypiIndexURL()
        XCTAssertNil(npm)
        XCTAssertNil(pypi)
        let env = await adapter.environmentOverrides()
        XCTAssertTrue(env.isEmpty)
    }

    // MARK: - 开着加速但还没探测

    func test_mirrorOn_butNotProbed_usesNothing() async {
        // **没探测过就不该假装有镜像。** 以前这里是硬编码列表里的第一个，
        // 那个源不通也照用。
        let adapter = CNNetworkAdapter(options: CNNetworkAdapter.Options(mirrorCN: true))
        let original = URL(string: "https://github.com/foo/bar")!
        let result = await adapter.githubURL(original)
        XCTAssertEqual(result, original)
        let plan = await adapter.currentPlan()
        XCTAssertTrue(plan.isEmpty)
    }

    func test_turningMirrorOff_clearsThePlan() async {
        let adapter = CNNetworkAdapter(options: CNNetworkAdapter.Options(mirrorCN: true))
        await adapter.updateOptions(CNNetworkAdapter.Options(mirrorCN: false))
        let plan = await adapter.currentPlan()
        XCTAssertTrue(plan.isEmpty)
        let results = await adapter.currentProbeResults()
        XCTAssertTrue(results.isEmpty)
    }

    // MARK: - 探测

    func test_probeAll_withoutMirror_doesNothing() async {
        let adapter = CNNetworkAdapter(options: CNNetworkAdapter.Options(mirrorCN: false))
        let results = await adapter.probeAll()
        XCTAssertTrue(results.isEmpty)
    }

    func test_probeAll_whenMirrorOn_hasOneResultPerCandidate() async {
        // 这个测试会真的发请求，慢，但必须跑 —— 探测本身就是网络行为，
        // 离线测不到它有没有真的按候选表挨个来。
        let adapter = CNNetworkAdapter(options: CNNetworkAdapter.Options(mirrorCN: true))
        let results = await adapter.probeAll()
        let expected = CNMirrorKind.allCases.reduce(0) { total, kind in
            total + CNMirrorCatalog.candidates(of: kind).count
        }
        XCTAssertEqual(results.count, expected)
        // 每个候选都要有结论，不能有漏掉的。
        for kind in CNMirrorKind.allCases {
            for mirror in CNMirrorCatalog.candidates(of: kind) {
                XCTAssertTrue(results.contains { $0.mirror.id == mirror.id }, "漏了 \(mirror.name)")
            }
        }
    }

    func test_describe_doesNotMentionTLSVerification() async {
        // 「跳过 TLS 验证」那个假开关已经删掉了：它从来没真的跳过任何校验，
        // 却会让人以为证书校验被关了。
        let adapter = CNNetworkAdapter(options: CNNetworkAdapter.Options(mirrorCN: false))
        let text = await adapter.describe()
        XCTAssertFalse(text.contains("CA verify"))
        XCTAssertFalse(text.lowercased().contains("tls"))
    }
}
