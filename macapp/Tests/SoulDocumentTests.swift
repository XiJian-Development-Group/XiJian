import XCTest
@testable import XiJianManagerKit

final class SoulDocumentTests: XCTestCase {

    func test_parse_singleSection() {
        let md = "# 基本信息\n\n我叫 Alice，22 岁。\n"
        let doc = SoulDocument.parse(md)
        XCTAssertEqual(doc.sections.count, 1)
        XCTAssertEqual(doc.sections.first?.title, "基本信息")
        XCTAssertEqual(doc.sections.first?.content, "我叫 Alice，22 岁。")
    }

    func test_parse_multipleSections() {
        let md = """
        # 名字
        Alice

        # 性格
        温柔 / 理性

        # 背景
        从小在海边长大
        """
        let doc = SoulDocument.parse(md)
        XCTAssertEqual(doc.sections.count, 3)
        XCTAssertEqual(doc.sections.map { $0.title }, ["名字", "性格", "背景"])
        XCTAssertEqual(doc.sections[0].content, "Alice")
        XCTAssertEqual(doc.sections[1].content, "温柔 / 理性")
        XCTAssertEqual(doc.sections[2].content, "从小在海边长大")
    }

    func test_parse_noSections_createsUntitled() {
        let md = "我没有标题"
        let doc = SoulDocument.parse(md)
        XCTAssertEqual(doc.sections.count, 1)
        XCTAssertEqual(doc.sections.first?.title, "(untitled)")
        XCTAssertEqual(doc.sections.first?.content, "我没有标题")
    }

    func test_toMarkdown_roundtrip() {
        let original = """
        # 名字
        Alice

        # 性格
        温柔
        """
        let doc = SoulDocument.parse(original)
        let result = doc.toMarkdown()
        // 重新解析应当相等
        let reparsed = SoulDocument.parse(result)
        XCTAssertEqual(reparsed.sections.count, 2)
        XCTAssertEqual(reparsed.sections[0].title, "名字")
        XCTAssertEqual(reparsed.sections[1].title, "性格")
    }

    func test_saveAndLoad_roundtrip() throws {
        let tmp = FileManager.default.temporaryDirectory
            .appendingPathComponent("soul-test-\(UUID().uuidString).md")
        defer { try? FileManager.default.removeItem(at: tmp) }

        let doc = SoulDocument(sections: [
            .init(title: "名字", content: "Alice"),
            .init(title: "性格", content: "温柔"),
        ])
        try doc.save(to: tmp)
        let loaded = try SoulDocument.load(from: tmp)
        XCTAssertEqual(loaded.sections.count, 2)
        XCTAssertEqual(loaded.sections[0].content, "Alice")
    }

    func test_load_nonexistent_returnsEmpty() throws {
        let tmp = FileManager.default.temporaryDirectory
            .appendingPathComponent("does-not-exist-\(UUID().uuidString).md")
        let doc = try SoulDocument.load(from: tmp)
        XCTAssertTrue(doc.sections.isEmpty)
    }
}
