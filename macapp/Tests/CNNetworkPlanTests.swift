import XCTest
@testable import XiJianManagerKit

/// 加速方案：怎么选、怎么变成环境变量。
///
/// 这一层是纯逻辑，不发请求，所以能离线测。
final class CNNetworkPlanTests: XCTestCase {

    // MARK: - 选源

    func test_plan_takesTheFirstUsableOfEachKind() {
        let pypiBad = CNMirrorCatalog.pypi[0]
        let pypiGood = CNMirrorCatalog.pypi[1]
        let gitGood = CNMirrorCatalog.git[0]

        let plan = CNNetworkPlanner.plan(from: [
            CNProbeResult(mirror: pypiBad, reachable: false),
            CNProbeResult(mirror: pypiGood, reachable: true),
            CNProbeResult(mirror: gitGood, reachable: true),
        ])

        XCTAssertEqual(plan.pypi?.name, pypiGood.name)
        XCTAssertEqual(plan.git?.name, gitGood.name)
    }

    func test_plan_leavesAKindEmptyWhenNothingWorks() {
        let plan = CNNetworkPlanner.plan(from: [
            CNProbeResult(mirror: CNMirrorCatalog.pypi[0], reachable: false),
        ])
        XCTAssertNil(plan.pypi)
        XCTAssertNil(plan.git)
        XCTAssertTrue(plan.isEmpty)
    }

    func test_plan_keepsThePriorityOrderOfTheCatalog() {
        // 候选表是按「实测更可靠」排的，不是按这次延迟排的。
        let results = CNMirrorCatalog.pypi.map {
            CNProbeResult(mirror: $0, reachable: true)
        }
        let plan = CNNetworkPlanner.plan(from: results)
        XCTAssertEqual(plan.pypi?.name, CNMirrorCatalog.pypi.first?.name)
    }

    // MARK: - 环境变量

    func test_environment_isEmptyWithoutAPlan() {
        XCTAssertTrue(CNNetworkPlanner.environment(for: CNNetworkPlan()).isEmpty)
    }

    func test_environment_carriesThePythonIndex() {
        let plan = CNNetworkPlan(pypi: CNMirrorCatalog.pypi[0])
        let env = CNNetworkPlanner.environment(for: plan)
        XCTAssertEqual(env["UV_INDEX_URL"], CNMirrorCatalog.pypi[0].url)
        XCTAssertEqual(env["PIP_INDEX_URL"], CNMirrorCatalog.pypi[0].url)
        XCTAssertEqual(env["UV_DEFAULT_INDEX"], CNMirrorCatalog.pypi[0].url)
    }

    func test_environment_carriesTheRawBase() {
        let plan = CNNetworkPlan(raw: CNMirrorCatalog.raw[0])
        let env = CNNetworkPlanner.environment(for: plan)
        XCTAssertEqual(env["HERMES_RAW_BASE"], CNMirrorCatalog.raw[0].url)
    }

    func test_environment_carriesTheGitRewrite() {
        let plan = CNNetworkPlan(git: CNMirrorCatalog.git[0])
        let env = CNNetworkPlanner.environment(for: plan)
        let count = Int(env["GIT_CONFIG_COUNT"] ?? "0") ?? 0
        XCTAssertGreaterThan(count, 0)
        for index in 0..<count {
            let key = env["GIT_CONFIG_KEY_\(index)"]
            let value = env["GIT_CONFIG_VALUE_\(index)"]
            XCTAssertNotNil(key)
            XCTAssertNotNil(value)
            // 键是「url.<镜像>.insteadOf」，值是被换掉的上游地址。
            XCTAssertTrue(key!.hasPrefix("url."))
            XCTAssertTrue(key!.hasSuffix(".insteadOf"))
            XCTAssertTrue(key!.contains(CNMirrorCatalog.git[0].url))
            XCTAssertTrue(value!.hasPrefix("https://github.com/NousResearch/hermes-agent"))
        }
    }

    func test_environment_pairsTheGitKeysAndValues() {
        let plan = CNNetworkPlan(git: CNMirrorCatalog.git[0])
        let env = CNNetworkPlanner.environment(for: plan)
        let count = Int(env["GIT_CONFIG_COUNT"] ?? "0") ?? 0
        for index in 0..<count {
            XCTAssertNotNil(env["GIT_CONFIG_KEY_\(index)"])
            XCTAssertNotNil(env["GIT_CONFIG_VALUE_\(index)"])
        }
        // 计数之外不能有落单的键。
        for index in count..<(count + 3) {
            XCTAssertNil(env["GIT_CONFIG_KEY_\(index)"])
            XCTAssertNil(env["GIT_CONFIG_VALUE_\(index)"])
        }
    }

    // MARK: - 地址换算

    func test_archiveURL_replacesTheAPITarball() {
        let tarball = URL(string: "https://api.github.com/repos/NousResearch/hermes-agent/tarball/v1.2.3")!
        let archive = CNNetworkPlanner.archiveURL(fromTarball: tarball)
        XCTAssertEqual(
            archive?.absoluteString,
            "https://github.com/NousResearch/hermes-agent/archive/refs/tags/v1.2.3.tar.gz"
        )
    }

    func test_archiveURL_returnsNilForSomethingElse() {
        let other = URL(string: "https://example.com/something.tar.gz")!
        XCTAssertNil(CNNetworkPlanner.archiveURL(fromTarball: other))
    }

    func test_mirroredGitHubURL_prefixesTheMirror() {
        let original = URL(string: "https://github.com/NousResearch/hermes-agent/archive/refs/tags/v1.2.3.tar.gz")!
        let mirrored = CNNetworkPlanner.mirroredGitHubURL(original, using: CNMirrorCatalog.git[0])
        XCTAssertTrue(mirrored.absoluteString.hasPrefix(CNMirrorCatalog.git[0].url))
        XCTAssertTrue(mirrored.absoluteString.contains("github.com/NousResearch/hermes-agent"))
    }

    func test_mirroredGitHubURL_withoutMirror_returnsOriginal() {
        let original = URL(string: "https://github.com/foo/bar")!
        XCTAssertEqual(CNNetworkPlanner.mirroredGitHubURL(original, using: nil), original)
    }

    // MARK: - 候选表本身

    func test_everyPythonMirrorHasAPackagesPrefix() {
        // **这是硬性要求。** Hermes 的 uv.lock 里固化了三千多处
        // files.pythonhosted.org/packages/... 绝对地址，镜像只有逐字节保持
        // 同样的目录结构，才能把前缀换掉。
        for mirror in CNMirrorCatalog.pypi {
            XCTAssertNotNil(mirror.packagesPrefix, "\\(mirror.name) 少了 packages 前缀")
            XCTAssertTrue(mirror.packagesPrefix!.hasSuffix("/packages/"), "\\(mirror.name) 的前缀结尾不对")
        }
    }

    func test_everyMirrorHasAProbeAddress() {
        for kind in CNMirrorKind.allCases {
            for mirror in CNMirrorCatalog.candidates(of: kind) {
                XCTAssertFalse(mirror.probeURL.isEmpty, "\\(mirror.name) 没有探测地址")
            }
        }
    }

    func test_gitMirrorsAreProbedWithGit() {
        // HTTP 200 不算数：有的代理返回的是反爬挑战页，clone 只会得到空仓库。
        for mirror in CNMirrorCatalog.git {
            XCTAssertEqual(mirror.probeMethod, .git)
        }
    }

    func test_theCatalogIsNotEmpty() {
        XCTAssertFalse(CNMRCatalogIsEmpty.pypi)
    }
}

/// 一个小小的存在性检查，避免上面那条测试写得太绕。
enum CNMRCatalogIsEmpty {
    static var pypi: Bool { CNMirrorCatalog.pypi.isEmpty }
}
