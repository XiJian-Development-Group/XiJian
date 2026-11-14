import XCTest
@testable import XiJianManagerKit

/// 代理和国内镜像是两件独立的事。
///
/// 以前 `environmentOverrides()` 一句 `guard options.mirrorCN else { return [:] }`
/// 把代理一起丢了 —— 用户自己有梯子、但没开镜像的时候，代理等于没填。
final class CNProxyTests: XCTestCase {

    func testProxyReachesTheEnvironmentEvenWithoutTheMirror() async {
        let adapter = CNNetworkAdapter(options: CNNetworkAdapter.Options(
            mirrorCN: false, proxyURL: "http://127.0.0.1:7890"))
        let env = await adapter.environmentOverrides()
        XCTAssertEqual(env["HTTP_PROXY"], "http://127.0.0.1:7890")
        XCTAssertEqual(env["HTTPS_PROXY"], "http://127.0.0.1:7890")
        XCTAssertEqual(env["ALL_PROXY"], "http://127.0.0.1:7890")
        XCTAssertEqual(env["NO_PROXY"], "localhost,127.0.0.1,::1")
    }

    func testNoProxyMeansNoProxyVariables() async {
        let adapter = CNNetworkAdapter(options: CNNetworkAdapter.Options())
        let env = await adapter.environmentOverrides()
        XCTAssertNil(env["HTTP_PROXY"])
        XCTAssertNil(env["NO_PROXY"])
    }

    func testBlankProxyIsIgnored() async {
        let adapter = CNNetworkAdapter(options: CNNetworkAdapter.Options(proxyURL: "   "))
        let env = await adapter.environmentOverrides()
        XCTAssertNil(env["HTTP_PROXY"])
    }
}
