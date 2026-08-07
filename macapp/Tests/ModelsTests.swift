import XCTest
@testable import XiJianKit

/// Models 解码测试：与 docs/api.md 响应结构对齐
final class ModelsTests: XCTestCase {

    private let decoder = JSONDecoder()

    // MARK: - 模型

    func testModelInfoDecodes() throws {
        let json = """
        {"id":"qwen2.5-7b-mlx-4bit","object":"model","created":1718000000,"owned_by":"xijian",
         "xijian":{"backend":"mlx","family":"qwen2.5","size_b":7.0,"quant":"4bit","context_length":32768,"min_ram_gb":8,"loaded":true}}
        """
        let model = try decoder.decode(ModelInfo.self, from: Data(json.utf8))
        XCTAssertEqual(model.id, "qwen2.5-7b-mlx-4bit")
        XCTAssertEqual(model.xijian?.backend, "mlx")
        XCTAssertEqual(model.xijian?.quant, "4bit")
        XCTAssertEqual(model.xijian?.loaded, true)
        XCTAssertEqual(model.displayName, "qwen2.5-7b-mlx-4bit (4bit)")
    }

    // MARK: - 消息与会话

    func testChatMessageDecodes() throws {
        let json = """
        {"id":"msg_1","object":"session.message","session_id":"sess_1","role":"assistant","content":"你好呀~","created_at":1718000000}
        """
        let message = try decoder.decode(ChatMessage.self, from: Data(json.utf8))
        XCTAssertEqual(message.role, "assistant")
        XCTAssertTrue(message.isAssistant)
        XCTAssertFalse(message.isUser)
        XCTAssertEqual(message.content, "你好呀~")
    }

    func testSessionInfoDecodes() throws {
        let json = """
        {"id":"sess_1","object":"session","title":"新会话","created_at":1718000000,"updated_at":1718000001}
        """
        let session = try decoder.decode(SessionInfo.self, from: Data(json.utf8))
        XCTAssertEqual(session.id, "sess_1")
        XCTAssertEqual(session.title, "新会话")
    }

    // MARK: - 角色

    func testCharacterInfoDecodes() throws {
        let json = """
        {"id":"char_yuki","object":"character","name":"Yuki","display_name":"Yuki","persona_doc":"温柔细心","voice_profile":"v1","default_emotion":"neutral","tags":["demo","default"],"loaded":true,"created_at":1718000000,"updated_at":1718000000}
        """
        let character = try decoder.decode(CharacterInfo.self, from: Data(json.utf8))
        XCTAssertEqual(character.id, "char_yuki")
        XCTAssertEqual(character.displayName, "Yuki")
        XCTAssertTrue(character.isLoaded)
        XCTAssertEqual(character.tagList, ["demo", "default"])
    }

    func testCharacterStateDecodesDynamicFields() throws {
        let json = """
        {"intimacy": 42.5, "mood": 80, "energy": 65, "recent_memory_summary": "最近聊了天", "flags": {"awake": true}}
        """
        let state = try decoder.decode(CharacterStateInfo.self, from: Data(json.utf8))
        XCTAssertEqual(state.intimacy, 42.5)
        XCTAssertEqual(state.mood, 80)
        XCTAssertEqual(state["recent_memory_summary"]?.stringValue, "最近聊了天")
        XCTAssertEqual(state.sortedEntries.count, 5)
    }

    // MARK: - 世界

    func testWorldInfoDecodes() throws {
        let json = """
        {"id":"world_modern_tokyo","name":"Modern Tokyo","world_doc_path":"worlds/modern_tokyo/lore.md","config_path":"worlds/modern_tokyo/config.json","state_doc_path":"worlds/modern_tokyo/state.json","is_active":true,"created_at":1718000000,"updated_at":1718000000}
        """
        let world = try decoder.decode(WorldInfo.self, from: Data(json.utf8))
        XCTAssertEqual(world.worldID, "world_modern_tokyo")
        XCTAssertTrue(world.isActive)
    }

    func testWorldStateInfoDecodes() throws {
        let json = """
        {"world_id":"world_modern_tokyo","name":"Modern Tokyo","is_active":true,"world_doc_path":"lore.md","config_path":"config.json","state_doc_path":"state.json",
         "environment":{"weather":"sunny","time_of_day":"evening","light_level":0.8,"ambient_audio":"city","env_meta":{"a":1}},
         "compute_config":{"active_tier":"low","max_npcs":10,"max_active_npcs":3,"max_low_active_npcs":7,"total_token_budget":4096},
         "npc_count":2,"updated_at":1718000000,
         "economy": 100, "health": 90, "mentality": 75}
        """
        let state = try decoder.decode(WorldStateInfo.self, from: Data(json.utf8))
        XCTAssertEqual(state.world_id, "world_modern_tokyo")
        XCTAssertEqual(state.environment?.weather, "sunny")
        XCTAssertEqual(state.compute_config?.max_npcs, 10)
        XCTAssertEqual(state.npc_count, 2)
        // 自由字段应进入 extra
        XCTAssertEqual(state.extra["economy"]?.doubleValue, 100)
        XCTAssertEqual(state.extra["health"]?.doubleValue, 90)
        XCTAssertEqual(state.extra["mentality"]?.doubleValue, 75)
    }

    // MARK: - 记忆

    func testMemoryEntryDecodes() throws {
        let json = """
        {"id":"mem_1","object":"memory.entry","character_id":"char_yuki","type":"long","content":"用户喜欢草莓味冰淇淋","importance":0.9,"source":"manual","tags":["food","ice_cream"],"access_count":3,"decay_score":1.0,"created_at":1718000000,"updated_at":1718000000,"deleted_at":null,
         "attributes":{"importance":"high","decay":"never","category":"preference"}}
        """
        let entry = try decoder.decode(MemoryEntry.self, from: Data(json.utf8))
        XCTAssertEqual(entry.id, "mem_1")
        XCTAssertEqual(entry.type, "long")
        XCTAssertEqual(entry.typeDisplay, "长期")
        XCTAssertEqual(entry.importance, 0.9)
        XCTAssertEqual(entry.tagList, ["food", "ice_cream"])
        XCTAssertEqual(entry.attributes?["category"]?.stringValue, "preference")
    }

    // MARK: - 分页信封

    func testListEnvelopeDecodes() throws {
        let json = """
        {"object":"list","data":[{"id":"char_a","object":"character"}],"has_more":false,"first_id":"char_a","last_id":"char_a"}
        """
        let envelope = try decoder.decode(ListEnvelope<CharacterInfo>.self, from: Data(json.utf8))
        XCTAssertEqual(envelope.data.count, 1)
        XCTAssertEqual(envelope.data.first?.id, "char_a")
        XCTAssertEqual(envelope.has_more, false)
    }

    // MARK: - 安全

    func testGateStatusDecodes() throws {
        let json = """
        {"enabled":true,"guard_level":"standard","audit_log_size":1234,"version":"1.0.0"}
        """
        let status = try decoder.decode(GateStatus.self, from: Data(json.utf8))
        XCTAssertTrue(status.enabled)
        XCTAssertEqual(status.guard_level, "standard")
    }

    func testDisableChallengeDecodes() throws {
        let json = """
        {"challenge_id":"chal_abc","expires_at":1718000900,"challenge_phrase":"关闭保护 Yuki"}
        """
        let challenge = try decoder.decode(DisableChallenge.self, from: Data(json.utf8))
        XCTAssertEqual(challenge.challenge_id, "chal_abc")
        XCTAssertEqual(challenge.challenge_phrase, "关闭保护 Yuki")
    }

    func testSafetyScanResultDecodes() throws {
        let json = """
        {"verdict":"block","blocked":"injection_pattern","matches":[{"id":"rule_1","pattern":"test"}],"audit_id":"audit_1"}
        """
        let result = try decoder.decode(SafetyScanResult.self, from: Data(json.utf8))
        XCTAssertTrue(result.isBlock)
        XCTAssertFalse(result.isPass)
        XCTAssertEqual(result.blocked, "injection_pattern")
    }

    // MARK: - 备份

    func testProtectedModuleDecodes() throws {
        let json = """
        {"module_name":"memory_entries","description":"记忆条目","auto_backup":true,"last_backup_at":1718000000}
        """
        let module = try decoder.decode(ProtectedModule.self, from: Data(json.utf8))
        XCTAssertEqual(module.module_name, "memory_entries")
        XCTAssertEqual(module.auto_backup, true)
        XCTAssertEqual(module.id, "memory_entries")
    }

    func testBackupRecordDecodes() throws {
        let json = """
        {"backup_id":"bk_1","character_id":"char_yuki","scope":"all","created_by":"user","file_path":"/tmp/x.bak","size_bytes":1024,"created_at":1718000000}
        """
        let backup = try decoder.decode(BackupRecord.self, from: Data(json.utf8))
        XCTAssertEqual(backup.backup_id, "bk_1")
        XCTAssertEqual(backup.scope, "all")
        XCTAssertEqual(backup.size_bytes, 1024)
    }

    // MARK: - 剧情

    func testPlotDesignDecodes() throws {
        let json = """
        {"plot_id":"plot_demo","title":"Demo","node_count":3,"edge_count":2}
        """
        let design = try decoder.decode(PlotDesign.self, from: Data(json.utf8))
        XCTAssertEqual(design.plot_id, "plot_demo")
        XCTAssertEqual(design.node_count, 3)
    }

    func testPlotRuntimeDecodes() throws {
        let json = """
        {"runtime_id":"rt_1","plot_id":"plot_demo","world_id":"world_1","status":"running","current_node_id":"node_1","variables":{"player_name":"阿月"}}
        """
        let runtime = try decoder.decode(PlotRuntime.self, from: Data(json.utf8))
        XCTAssertEqual(runtime.runtime_id, "rt_1")
        XCTAssertEqual(runtime.status, "running")
        XCTAssertEqual(runtime.variables?["player_name"]?.stringValue, "阿月")
    }

    // MARK: - 流式块

    func testChatStreamChunkDecodes() throws {
        let json = """
        {"id":"chatcmpl-9f8a","object":"chat.completion.chunk","created":1718000000,"model":"m","choices":[{"index":0,"delta":{"content":"你好"},"finish_reason":null}]}
        """
        let chunk = try decoder.decode(ChatStreamChunk.self, from: Data(json.utf8))
        XCTAssertEqual(chunk.deltaContent, "你好")
        XCTAssertNil(chunk.finishReason)
    }

    func testChatStreamChunkFinishReason() throws {
        let json = """
        {"id":"chatcmpl-9f8a","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}
        """
        let chunk = try decoder.decode(ChatStreamChunk.self, from: Data(json.utf8))
        XCTAssertEqual(chunk.finishReason, "stop")
        XCTAssertEqual(chunk.deltaContent, "")
    }

    func testChatStreamChunkUsage() throws {
        let json = """
        {"id":"chatcmpl-9f8a","object":"chat.completion.chunk","choices":[],"usage":{"prompt_tokens":128,"completion_tokens":18,"total_tokens":146}}
        """
        let chunk = try decoder.decode(ChatStreamChunk.self, from: Data(json.utf8))
        XCTAssertEqual(chunk.usage?.total_tokens, 146)
        XCTAssertTrue(chunk.choices?.isEmpty == true)
    }

    // MARK: - JSONValue

    func testJSONValueDecodesAllTypes() throws {
        let json = #"{"s":"文本","n":3.14,"i":42,"b":true,"nil":null,"arr":[1,2],"obj":{"k":"v"}}"#
        let values = try decoder.decode([String: JSONValue].self, from: Data(json.utf8))
        XCTAssertEqual(values["s"], .string("文本"))
        XCTAssertEqual(values["n"], .number(3.14))
        XCTAssertEqual(values["i"], .number(42))
        XCTAssertEqual(values["b"], .bool(true))
        XCTAssertEqual(values["nil"], .null)
        XCTAssertEqual(values["arr"], .array([.number(1), .number(2)]))
        XCTAssertEqual(values["obj"], .object(["k": .string("v")]))
        // displayText
        XCTAssertEqual(values["s"]?.displayText, "文本")
        XCTAssertEqual(values["i"]?.displayText, "42")
        XCTAssertEqual(values["b"]?.displayText, "是")
    }

    // MARK: - 设置

    func testServerSettingsDecodesEmptyAndFilled() throws {
        let empty = try decoder.decode(ServerSettings.self, from: Data("{}".utf8))
        XCTAssertTrue(empty.isEmpty)

        let json = #"{"default_model":"qwen2.5-7b","temperature":0.8}"#
        let settings = try decoder.decode(ServerSettings.self, from: Data(json.utf8))
        XCTAssertEqual(settings["default_model"]?.stringValue, "qwen2.5-7b")
        XCTAssertEqual(settings["temperature"]?.doubleValue, 0.8)
    }

    // MARK: - 时间工具

    func testXijianDateConversion() {
        let date = 1718000000.0.xijianDate
        XCTAssertEqual(date.timeIntervalSince1970, 1718000000)
        // 时间文本应非空
        XCTAssertFalse(date.xijianTimeText.isEmpty)
    }
}
