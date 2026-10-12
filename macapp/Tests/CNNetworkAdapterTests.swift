import XCTest
@testable import XiJianManagerKit

final class CNNetworkAdapterTests: XCTestCase {

    func test_githubURL_withoutMirrorCN_returnsOriginal() async {
        let adapter = CNNetworkAdapter(options: CNNetworkAdapter.Options(mirrorCN: false))
        let original = URL(string: "https://github.com/foo/bar")!
        let result = await adapter.githubURL(original)
        XCTAssertEqual(result, original)
    }

    func test_githubURL_withMirrorCN_returnsMirror() async {
        let adapter = CNNetworkAdapter(options: CNNetworkAdapter.Options(mirrorCN: true))
        let original = URL(string: "https://github.com/foo/bar")!
        let result = await adapter.githubURL(original)
        XCTAssertNotNil(result)
        XCTAssertNotEqual(result, original)
        XCTAssertTrue(result!.absoluteString.contains("github.com/foo/bar"))
    }

    func test_npmRegistry_returnsTsinghuaWhenEnabled() async {
        let adapter = CNNetworkAdapter(options: CNNetworkAdapter.Options(mirrorCN: true))
        let registry = await adapter.npmRegistry()
        XCTAssertNotNil(registry)
        XCTAssertTrue(registry!.contains("npmmirror"))
    }

    func test_pypiIndexURL_returnsTunaWhenEnabled() async {
        let adapter = CNNetworkAdapter(options: CNNetworkAdapter.Options(mirrorCN: true))
        let url = await adapter.pypiIndexURL()
        XCTAssertNotNil(url)
        XCTAssertTrue(url!.contains("tuna"))
    }

    func test_options_disabled_returnsNilForMirrors() async {
        let adapter = CNNetworkAdapter(options: CNNetworkAdapter.Options(mirrorCN: false))
        let npm = await adapter.npmRegistry()
        let pypi = await adapter.pypiIndexURL()
        XCTAssertNil(npm)
        XCTAssertNil(pypi)
    }

    func test_updateOptions_changesBehavior() async {
        let adapter = CNNetworkAdapter(options: CNNetworkAdapter.Options(mirrorCN: false))
        let npmBefore = await adapter.npmRegistry()
        XCTAssertNil(npmBefore)
        await adapter.updateOptions(CNNetworkAdapter.Options(mirrorCN: true))
        let npmAfter = await adapter.npmRegistry()
        XCTAssertNotNil(npmAfter)
    }
}
