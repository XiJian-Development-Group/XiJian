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

    // MARK: - 角色状态摘要（A3.2）

    func testCharacterStateSummaryDecodes() throws {
        // 完整 A3.2 形状：顶层 values/max/status/can_dialogue
        let json = """
        {"character_id":"char_yuki","affection":50,"mood":"neutral","recent_memory_summary":"最近的互动：...","updated_at":1718000000,
         "values":{"hunger":72.0,"thirst":45.0,"health":100.0,"mood":88.0,"stamina":60.0},
         "max":{"hunger":100.0,"thirst":100.0,"health":100.0,"mood":100.0,"stamina":100.0},
         "status":"healthy","status_changed_at":1718000000,"last_updated":1718000001,
         "can_dialogue":true,"active_behavior":[],"modifiers":{"time_modifier":1.0}}
        """
        let state = try decoder.decode(CharacterStateInfo.self, from: Data(json.utf8))
        let summary = try XCTUnwrap(state.summary, "应解析出状态摘要")
        XCTAssertEqual(summary.value(for: .hunger), 72.0)
        XCTAssertEqual(summary.value(for: .thirst), 45.0)
        XCTAssertEqual(summary.max(for: .mood), 100.0)
        XCTAssertEqual(summary.status, "healthy")
        XCTAssertEqual(summary.statusDisplayName, "健康")
        XCTAssertTrue(summary.canDialogue == true)
        XCTAssertFalse(summary.isCritical)
    }

    func testCharacterStateSummaryMissingValuesIsNil() throws {
        // v1 形状（角色从未被状态系统触碰）：无 values 块 → 摘要为 nil
        let json = """
        {"character_id":"char_new","affection":50,"mood":"neutral","recent_memory_summary":"新角色","updated_at":1718000000}
        """
        let state = try decoder.decode(CharacterStateInfo.self, from: Data(json.utf8))
        XCTAssertNil(state.summary)
    }

    func testCharacterStateSummaryCritical() throws {
        let json = """
        {"values":{"hunger":0,"thirst":0,"health":0,"mood":10},"max":{"health":100},"status":"critical","can_dialogue":false}
        """
        let state = try decoder.decode(CharacterStateInfo.self, from: Data(json.utf8))
        let summary = try XCTUnwrap(state.summary)
        XCTAssertTrue(summary.isCritical)
        XCTAssertEqual(summary.statusDisplayName, "危殆")
        XCTAssertFalse(summary.canDialogue == true)
        // 缺失 max 的维度回退默认 100
        XCTAssertEqual(summary.max(for: .hunger), 100.0)
    }

    func testCharacterStateLogEntryDecodes() throws {
        let json = """
        [{"id":"log_1","character_id":"char_yuki","field":"hunger","old_value":80.0,"new_value":72.0,
          "reason":"tick","ref_id":null,"created_at":1718000000}]
        """
        let entries = try decoder.decode([CharacterStateLogEntry].self, from: Data(json.utf8))
        XCTAssertEqual(entries.count, 1)
        let entry = try XCTUnwrap(entries.first)
        XCTAssertEqual(entry.field, "hunger")
        XCTAssertEqual(entry.fieldDisplayName, "饱食")
        XCTAssertEqual(entry.reasonDisplayName, "自然衰减")
        XCTAssertEqual(entry.deltaText, "-8")
        XCTAssertEqual(entry.deltaSign, -1)
    }

    func testCharacterStateLogEntryPositiveDelta() throws {
        let json = """
        {"id":"log_2","field":"health","old_value":60.0,"new_value":75.0,"reason":"manual","created_at":1}
        """
        let entry = try decoder.decode(CharacterStateLogEntry.self, from: Data(json.utf8))
        XCTAssertEqual(entry.deltaText, "+15")
        XCTAssertEqual(entry.deltaSign, 1)
        XCTAssertEqual(entry.reasonDisplayName, "手动调整")
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

    // MARK: - 资源包

    func testPackManifestDecodes() throws {
        let json = """
        {"schema":"xijian-pack/1","package_id":"char_yuki","name":"Yuki 角色包","version":"1.0.0","kind":"character","author":"mofan","description":"演示角色","dependencies":["base"],"created_at":"2026-06-01T00:00:00Z","files":["characters/yuki/character.json"]}
        """
        let manifest = try decoder.decode(PackManifest.self, from: Data(json.utf8))
        XCTAssertEqual(manifest.package_id, "char_yuki")
        XCTAssertEqual(manifest.name, "Yuki 角色包")
        XCTAssertEqual(manifest.version, "1.0.0")
        XCTAssertEqual(manifest.kind, "character")
        XCTAssertEqual(manifest.effectiveKind, "character")
        XCTAssertTrue(manifest.hasCharacters)
        XCTAssertFalse(manifest.hasWorlds)
        XCTAssertEqual(manifest.files?.count, 1)
    }

    func testPackManifestKindMissingIgnoresDevKitFields() throws {
        // kind 缺失 + DevKit submission 字段：额外字段应被忽略并默认 mixed
        let json = """
        {"name":"纯包","version":"0.1.0","developer_id":"dev-1","submitted_at":"2026-06-02T00:00:00Z","ai_ratio":0.3,"notes":"内部提交"}
        """
        let manifest = try decoder.decode(PackManifest.self, from: Data(json.utf8))
        XCTAssertEqual(manifest.name, "纯包")
        XCTAssertEqual(manifest.version, "0.1.0")
        XCTAssertNil(manifest.kind)
        XCTAssertEqual(manifest.effectiveKind, "mixed")
        XCTAssertEqual(manifest.developer_id, "dev-1")
        XCTAssertEqual(manifest.submitted_at, "2026-06-02T00:00:00Z")
        XCTAssertEqual(manifest.ai_ratio, 0.3)
        XCTAssertEqual(manifest.notes, "内部提交")
        XCTAssertTrue(manifest.hasCharacters)
        XCTAssertTrue(manifest.hasWorlds)
    }

    func testPackInfoDecodesWithNestedManifest() throws {
        let json = """
        {"package_id":"char_yuki","kind":"mixed","name":"Yuki 组合包","version":"2.1.0","path":"/data/packs/char_yuki","loaded":true,
         "manifest":{"schema":"xijian-pack/1","package_id":"char_yuki","name":"Yuki 组合包","version":"2.1.0","kind":"mixed","description":"含角色与世界"}}
        """
        let pack = try decoder.decode(PackInfo.self, from: Data(json.utf8))
        XCTAssertEqual(pack.package_id, "char_yuki")
        XCTAssertEqual(pack.id, "char_yuki")
        XCTAssertEqual(pack.kind, "mixed")
        XCTAssertEqual(pack.displayKind, "混合")
        XCTAssertTrue(pack.loaded)
        XCTAssertEqual(pack.path, "/data/packs/char_yuki")
        XCTAssertEqual(pack.manifest.description, "含角色与世界")
        XCTAssertEqual(pack.manifest.effectiveKind, "mixed")
        XCTAssertTrue(pack.hasCharacters)
        XCTAssertTrue(pack.hasWorlds)
    }

    func testImportJobInfoCompletedDecodesWithResult() throws {
        let json = """
        {"id":"imp_1","object":"resource.import","status":"completed","kind":"mixed","name":"组合包","file_id":"f_1","package_id":"char_yuki","created_at":1718000000.5,"completed_at":1718000010,
         "result":{"kind":"mixed","loaded_characters":1,"loaded_worlds":2,"loaded_memories":3}}
        """
        let job = try decoder.decode(ImportJobInfo.self, from: Data(json.utf8))
        XCTAssertEqual(job.id, "imp_1")
        XCTAssertTrue(job.isCompleted)
        XCTAssertFalse(job.isFailed)
        XCTAssertEqual(job.package_id, "char_yuki")
        XCTAssertEqual(job.result?.loaded_characters, 1)
        XCTAssertEqual(job.result?.loaded_worlds, 2)
        XCTAssertEqual(job.result?.loaded_memories, 3)
    }

    func testImportJobInfoFailedDecodesWithError() throws {
        let json = """
        {"id":"imp_2","object":"resource.import","status":"failed","name":"坏包","created_at":1718000000,"completed_at":1718000005,"error":"pack validation failed: missing manifest"}
        """
        let job = try decoder.decode(ImportJobInfo.self, from: Data(json.utf8))
        XCTAssertTrue(job.isFailed)
        XCTAssertFalse(job.isCompleted)
        XCTAssertEqual(job.error, "pack validation failed: missing manifest")
        XCTAssertNil(job.result)
    }

    func testImportJobInfoDecodesFromQueuedResponse() throws {
        // POST /v1/xijian/resources/import 的 202 响应只返回 job_id（无 id 键）
        let json = #"{"job_id":"imp_9","status":"queued"}"#
        let job = try decoder.decode(ImportJobInfo.self, from: Data(json.utf8))
        XCTAssertEqual(job.id, "imp_9")
        XCTAssertTrue(job.isRunning)
        XCTAssertEqual(job.status, "queued")
    }

    // MARK: - 来源标记（资源包）

    func testCharacterInfoDecodesPackSource() throws {
        let json = """
        {"id":"char_yuki","object":"character","name":"Yuki","display_name":"Yuki","persona_doc":"温柔细心","voice_profile":"v1","default_emotion":"neutral","tags":["demo","default"],"loaded":true,"created_at":1718000000,"updated_at":1718000000,"_pack_source":true,"_pack_id":"char_yuki-pack"}
        """
        let character = try decoder.decode(CharacterInfo.self, from: Data(json.utf8))
        XCTAssertTrue(character.isFromPack)
        XCTAssertEqual(character.packID, "char_yuki-pack")
    }

    func testCharacterInfoWithoutPackSourceDefaultsFalse() throws {
        let json = """
        {"id":"char_a","object":"character","name":"A","loaded":false}
        """
        let character = try decoder.decode(CharacterInfo.self, from: Data(json.utf8))
        XCTAssertFalse(character.isFromPack)
        XCTAssertNil(character.packID)
    }

    func testWorldInfoDecodesPackSource() throws {
        let json = """
        {"id":"world_1","name":"东京","world_doc_path":"w/lore.md","config_path":"w/config.json","state_doc_path":"w/state.json","is_active":true,"last_active_at":1718000000,"created_at":1718000000,"updated_at":1718000000,"_pack_source":true,"_pack_id":"world-pack"}
        """
        let world = try decoder.decode(WorldInfo.self, from: Data(json.utf8))
        XCTAssertTrue(world.isFromPack)
        XCTAssertEqual(world.packID, "world-pack")
        XCTAssertTrue(world.isActive)
    }
}
