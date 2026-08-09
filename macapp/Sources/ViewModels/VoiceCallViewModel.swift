import AVFoundation
import Combine
import Foundation

// MARK: - 客户端通话阶段

/// 客户端通话阶段（UI 状态机；服务端状态映射：idle/ringing → ringing，active → active，ended → ended）
enum VoiceCallPhase: Equatable {
    /// 无通话
    case idle
    /// 响铃中（拨出等待接听 / 来电）
    case ringing
    /// 通话中
    case active
    /// 已结束
    case ended

    var displayName: String {
        switch self {
        case .idle: return loc("未通话")
        case .ringing: return loc("响铃中")
        case .active: return loc("通话中")
        case .ended: return loc("通话结束")
        }
    }
}

// MARK: - 通话记录条目

/// 对话记录条目（供 VoiceCallView 展示；来源：本地发送回显 / WS `call.event` / REST 事件刷新）
struct VoiceCallTranscriptItem: Identifiable, Equatable {
    let id: String
    /// speech / song / barge_in / effect / system
    let kind: String
    /// user / assistant / system
    let role: String
    let text: String
    let turn: Int?
    let timestamp: Double?
    /// 附加信息（打断 / TTS 错误 / 歌曲状态等）
    let meta: String?

    var isUser: Bool { role == "user" }
    var isAssistant: Bool { role == "assistant" }
    var isSystem: Bool { role == "system" }
}

// MARK: - 通话 ViewModel

/// A6 实时通话 ViewModel — 组合 `VoiceCallService`（REST 状态机推进）与
/// `WebSocketClient`（订阅 `call.state_changed` / `call.event` 驱动状态与对话记录）。
///
/// 状态机：idle → ringing → active → ended。WS 为每通通话按需建立（连接时机在
/// `startCall`，`close()` 时断开）；服务端事件经 `call_id` 过滤，只消费本通话。
/// 对话记录去重：speech 条目按 `(role, turn)` 去重（REST 回显与 WS 推送可能重复），
/// 其余事件按服务端 `event_id` 去重。
@MainActor
final class VoiceCallViewModel: ObservableObject {

    // MARK: 对外状态

    /// 当前通话阶段
    @Published private(set) var phase: VoiceCallPhase = .idle
    /// 当前通话 ID
    @Published private(set) var callID: String?
    /// 通话角色 ID
    @Published private(set) var characterID: String?
    /// 通话角色名（由接入点传入，用于界面展示）
    @Published var characterName: String?
    /// 通话方向（user_initiated / character_initiated）
    @Published private(set) var direction: VoiceCallDirection?
    /// 通话开始时间戳（秒，展示通话时长用）
    @Published private(set) var startedAt: Double?
    /// 对话记录
    @Published private(set) var transcript: [VoiceCallTranscriptItem] = []
    /// barge-in 打断开关是否激活
    @Published private(set) var bargeInActive = false
    /// 当前轮次（服务端 current_turn）
    @Published private(set) var currentTurn = 0
    /// 是否有请求在途（界面禁用并发操作）
    @Published private(set) var isBusy = false
    /// 是否正在录音（麦克风采集）
    @Published private(set) var isRecording = false
    /// 是否正在播放 assistant 语音
    @Published private(set) var isPlayingAudio = false
    /// WebSocket 是否已连接（UI 提示用）
    @Published private(set) var isWSConnected = false
    /// 错误提示
    @Published var errorMessage: String?
    @Published var showError = false

    // MARK: 依赖

    private let service: VoiceCallServicing
    private let ws: WebSocketClient

    /// 麦克风录音器（@MainActor；录音本身不参与网络请求）
    private let audioRecorder = AudioRecorder()
    /// assistant 语音播放器
    private var audioPlayer: AVAudioPlayer?
    /// 播放完成回调桥接（避免 @MainActor 类直接实现非隔离的 AVAudioPlayerDelegate）
    private let playbackDelegate = VoiceCallAudioPlaybackDelegate()

    private var cancellables = Set<AnyCancellable>()

    // MARK: 内部状态

    /// speech 条目去重键（"user-N" / "assistant-N"）
    private var seenSpeechKeys = Set<String>()
    /// 非 speech 事件去重（服务端 event_id）
    private var seenEventIDs = Set<String>()

    // MARK: 初始化

    /// - Parameters:
    ///   - service: 通话服务（默认从 CoreManager.shared 构造，无 Core 时仍可创建，请求会失败并提示）
    ///   - ws: WebSocket 客户端（默认同 baseURL/token 新建；测试注入 Mock 传输）
    init(service: VoiceCallServicing? = nil, ws: WebSocketClient? = nil) {
        let core = CoreManager.shared
        let baseURL = core.baseURL
        let token = core.token ?? ""
        if let service {
            self.service = service
        } else {
            self.service = VoiceCallService(baseURL: baseURL, token: token)
        }
        if let ws {
            self.ws = ws
        } else {
            self.ws = WebSocketClient(baseURL: baseURL, token: token)
        }
        self.ws.onEvent = { [weak self] event in
            self?.handleWSEvent(event)
        }
        self.ws.$connectionState
            .sink { [weak self] state in
                self?.isWSConnected = (state == .connected)
            }
            .store(in: &cancellables)
        // 播放完成（或被新播放替换）后复位状态；闭包为非隔离上下文，显式跳回主线程
        playbackDelegate.onFinish = { [weak self] in
            Task { @MainActor in
                self?.audioPlayer = nil
                self?.isPlayingAudio = false
            }
        }
    }

    // MARK: 动作

    /// 拨出电话：创建通话（idle）→ 拨打（ringing），并建立 WS 连接订阅事件。
    /// 可从 idle / ended（重新拨打）发起。
    func startCall(characterId: String, characterName: String? = nil, direction: VoiceCallDirection = .userInitiated) async {
        guard !isBusy, phase != .ringing, phase != .active else { return }
        reset()
        self.characterID = characterId
        self.characterName = characterName
        self.direction = direction
        isBusy = true
        defer { isBusy = false }
        do {
            let record = try await service.createCall(characterId: characterId, direction: direction, userId: "local_user")
            callID = record.id
            startedAt = record.started_at
            ws.connect()
            let ringing = try await service.ring(callId: record.id)
            applyRecord(ringing)
        } catch {
            presentError(error)
            reset()
        }
    }

    /// 接听（ringing → active）
    func accept() async {
        guard let callID, !isBusy else { return }
        isBusy = true
        defer { isBusy = false }
        do {
            let record = try await service.accept(callId: callID)
            applyRecord(record)
        } catch {
            presentError(error)
        }
    }

    /// 拒绝（→ ended）
    func reject() async {
        guard let callID, !isBusy else { return }
        isBusy = true
        defer { isBusy = false }
        do {
            let record = try await service.reject(callId: callID)
            applyRecord(record)
            appendSystemLine(loc("已拒绝通话"))
        } catch {
            presentError(error)
        }
    }

    /// 挂断（active → ended）
    func end() async {
        guard let callID, !isBusy, phase != .ended else { return }
        isBusy = true
        defer { isBusy = false }
        do {
            let record = try await service.end(callId: callID)
            applyRecord(record)
            appendSystemLine(loc("通话已结束"))
        } catch {
            presentError(error)
        }
    }

    /// 发送文本（服务端 STT→AI→TTS 管线的 text 快捷路径）。
    /// 本地立即回显用户文本；AI 回复经 WS `call.event`（speech/assistant）异步到达，
    /// 同步模式下也直接取响应中的 `reply`。
    func sendText(_ text: String) async {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, let callID, phase == .active, !isBusy else { return }
        isBusy = true
        defer { isBusy = false }
        do {
            let result = try await service.sendSpeech(callId: callID, text: trimmed)
            if let turn = result.turn { currentTurn = turn }
            if let userText = result.user_text, !userText.isEmpty {
                ingestSpeech(role: "user", text: userText, turn: result.turn, meta: nil, eventID: result.user_event_id)
            }
            if let reply = result.reply, !reply.isEmpty {
                ingestSpeech(role: "assistant", text: reply, turn: result.turn, meta: nil, eventID: result.reply_event_id)
            } else if result.ok {
                // 异步管线：回复稍后经 WS 送达
                appendSystemLine(loc("回复生成中…"))
            }
        } catch {
            presentError(error)
        }
    }

    /// 开始录音：barge-in 语义先停止正在播放的 assistant 语音 → 请求权限 → 启动录音。
    /// 失败（无权限 / 启动失败）时复位录音态并展示错误。
    func startRecording() {
        guard phase == .active, !isBusy, !isRecording else { return }
        stopPlayback()  // 用户开口时中断 assistant 播放（barge-in）
        isRecording = true
        Task {
            let granted = await audioRecorder.requestPermission()
            guard granted else {
                isRecording = false
                presentError(AudioRecorder.RecordingError.permissionDenied)
                return
            }
            do {
                try audioRecorder.start()
            } catch {
                isRecording = false
                presentError(error)
            }
        }
    }

    /// 停止录音并上传语音（audio_base64 → 服务端 STT→AI→TTS）：
    /// 成功按 STT 回显用户语音文本并置回复生成中；失败展示错误（通话继续）。
    func stopRecordingAndSend() async {
        guard isRecording else { return }
        isRecording = false
        guard let callID, phase == .active, !isBusy else {
            audioRecorder.cancel()
            return
        }
        guard let audioData = audioRecorder.stop() else {
            presentError(AudioRecorder.RecordingError.readFailed)
            return
        }
        isBusy = true
        defer { isBusy = false }
        do {
            let result = try await service.sendAudio(callId: callID, audioData: audioData, language: "zh")
            if let turn = result.turn { currentTurn = turn }
            if let userText = result.user_text, !userText.isEmpty {
                ingestSpeech(role: "user", text: userText, turn: result.turn, meta: nil, eventID: result.user_event_id)
            }
            if let reply = result.reply, !reply.isEmpty {
                ingestSpeech(role: "assistant", text: reply, turn: result.turn, meta: nil, eventID: result.reply_event_id)
            } else if result.ok {
                // 异步管线：回复稍后经 WS 送达（speech/assistant，含 audio_base64）
                appendSystemLine(loc("回复生成中…"))
            } else if let error = result.error, !error.isEmpty {
                presentError(error)
            }
        } catch {
            presentError(error)
        }
    }

    /// 切换 barge-in 打断开关
    func toggleBargeIn() async {
        guard let callID, phase == .active, !isBusy else { return }
        isBusy = true
        defer { isBusy = false }
        do {
            let record = try await service.setBargeIn(callId: callID, active: !bargeInActive)
            bargeInActive = record.isBargeInActive
        } catch {
            presentError(error)
        }
    }

    /// 请求角色演唱（DiffSinger 接口桩；未接引擎时服务端返回 unavailable，仅记录不弹错）
    func sing(lyrics: String) async {
        let trimmed = lyrics.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, let callID, phase == .active, !isBusy else { return }
        isBusy = true
        defer { isBusy = false }
        do {
            let result = try await service.sing(callId: callID, lyrics: trimmed)
            let meta: String
            if result.ok {
                meta = loc("已送出")
            } else {
                meta = [result.status, result.reason].compactMap { $0 }.joined(separator: loc("："))
            }
            appendTranscript(item: VoiceCallTranscriptItem(
                id: "song-\(UUID().uuidString)",
                kind: "song",
                role: "assistant",
                text: "🎵 \(trimmed)",
                turn: nil,
                timestamp: Date().timeIntervalSince1970,
                meta: meta.isEmpty ? nil : meta
            ))
        } catch {
            presentError(error)
        }
    }

    /// 拉取通话事件并合并进对话记录（重连后补漏 / 手动刷新）
    func refresh() async {
        guard let callID else { return }
        do {
            let events = try await service.listEvents(callId: callID, limit: 100)
            for event in events {
                ingestEvent(eventID: event.id, kind: event.kind, payload: event.payloadObject ?? [:])
            }
        } catch {
            presentError(error)
        }
    }

    /// 清理（断开 WS、重置状态）；通话未结束时尽力通知服务端挂断。
    /// 由视图 `onDisappear` 调用。
    func close() {
        let activeCallID = callID
        let shouldEnd = phase == .active || phase == .ringing
        ws.disconnect()
        reset()
        if shouldEnd, let activeCallID {
            Task { try? await service.end(callId: activeCallID) }
        }
    }

    // MARK: WS 事件

    private func handleWSEvent(_ event: WebSocketEvent) {
        switch event.type {
        case "call.state_changed":
            isWSConnected = true
            guard let data = event.dataObject, let state = decodeStateView(data) else { return }
            applyStateView(state)
        case "call.event":
            isWSConnected = true
            guard let data = event.dataObject else { return }
            handleCallEvent(data)
        default:
            break
        }
    }

    /// 仅消费本通话的 `call.state_changed`（call_id 未知前丢弃——创建/拨打阶段的状态
    /// 已由 REST 响应直接应用，WS 只负责后续异步变更）
    private func applyStateView(_ state: VoiceCallStateView) {
        guard state.call_id == nil || state.call_id == callID else { return }
        switch state.statusEnum {
        case .ringing:
            phase = .ringing
        case .active:
            phase = .active
        case .ended:
            phase = .ended
            appendSystemLine(loc("通话已结束"))
        case .idle:
            break
        }
    }

    /// 消费 `call.event`：speech → 对话记录；song / barge_in / effect → 记录或提示
    private func handleCallEvent(_ data: [String: JSONValue]) {
        guard let eventCallID = data["call_id"]?.stringValue, eventCallID == callID else { return }
        let eventID = data["event_id"]?.stringValue
        let kind = data["kind"]?.stringValue ?? ""
        let payload = data["payload"]?.objectValue ?? [:]
        ingestEvent(eventID: eventID, kind: kind, payload: payload)
    }

    /// 事件合并入口（WS 推送与 REST 刷新共用；去重后写入对话记录）
    private func ingestEvent(eventID: String?, kind: String, payload: [String: JSONValue]) {
        switch kind {
        case "speech":
            let role = payload["role"]?.stringValue ?? "system"
            let text = payload["text"]?.stringValue ?? ""
            let turn = payload["turn"]?.doubleValue.map { Int($0) }
            var metaParts: [String] = []
            if payload["interrupted"]?.boolValue == true {
                metaParts.append(loc("被打断"))
            }
            if let error = payload["error"]?.stringValue {
                metaParts.append(loc("错误：%@", error))
            }
            if let ttsError = payload["tts_error"]?.stringValue {
                metaParts.append(loc("TTS：%@", ttsError))
            }
            ingestSpeech(
                role: role,
                text: text,
                turn: turn,
                meta: metaParts.isEmpty ? nil : metaParts.joined(separator: loc("；")),
                eventID: eventID,
                audioData: VoiceCallAudioPayload.audioData(from: payload)
            )
        case "song":
            let lyrics = payload["lyrics"]?.stringValue ?? ""
            let status = payload["status"]?.stringValue ?? ""
            let reason = payload["reason"]?.stringValue ?? ""
            let meta = [status.isEmpty ? nil : loc("状态：%@", status), reason.isEmpty ? nil : reason]
                .compactMap { $0 }.joined(separator: loc("；"))
            appendTranscript(item: VoiceCallTranscriptItem(
                id: eventID ?? "song-\(UUID().uuidString)",
                kind: kind,
                role: "assistant",
                text: lyrics.isEmpty ? loc("🎵（歌曲事件）") : "🎵 \(lyrics)",
                turn: nil,
                timestamp: Date().timeIntervalSince1970,
                meta: meta.isEmpty ? nil : meta
            ))
        case "barge_in":
            let active = payload["active"]?.boolValue ?? false
            bargeInActive = active
            appendTranscript(item: VoiceCallTranscriptItem(
                id: eventID ?? "barge-\(UUID().uuidString)",
                kind: kind,
                role: "system",
                text: active ? loc("已开启打断（barge-in）") : loc("已关闭打断"),
                turn: nil,
                timestamp: Date().timeIntervalSince1970,
                meta: nil
            ))
        default:
            break
        }
    }

    /// speech 条目：先按 (role, turn) / event_id 去重（占住去重键，防止重复记录与重复播放），
    /// 再写入对话记录；assistant 且携带 audio_base64 时播放语音。
    private func ingestSpeech(role: String, text: String, turn: Int?, meta: String?, eventID: String?, audioData: Data? = nil) {
        let dedupeKey: String?
        if let turn {
            let key = "\(role)-\(turn)"
            guard !seenSpeechKeys.contains(key) else { return }
            seenSpeechKeys.insert(key)
            dedupeKey = key
        } else if let eventID {
            guard !seenEventIDs.contains(eventID) else { return }
            seenEventIDs.insert(eventID)
            dedupeKey = eventID
        } else {
            dedupeKey = nil
        }

        if !text.isEmpty {
            appendTranscript(item: VoiceCallTranscriptItem(
                id: eventID ?? dedupeKey ?? "speech-\(UUID().uuidString)",
                kind: "speech",
                role: role,
                text: text,
                turn: turn,
                timestamp: Date().timeIntervalSince1970,
                meta: meta
            ))
        }

        // assistant 语音播放（barge-in 语义：用户开始录音时已 stop；播放前再防一次）
        if role == "assistant", let audioData, !audioData.isEmpty {
            playAssistantAudio(data: audioData)
        }
    }

    private func appendSystemLine(_ text: String) {
        appendTranscript(item: VoiceCallTranscriptItem(
            id: "sys-\(UUID().uuidString)",
            kind: "system",
            role: "system",
            text: text,
            turn: nil,
            timestamp: Date().timeIntervalSince1970,
            meta: nil
        ))
    }

    private func appendTranscript(item: VoiceCallTranscriptItem) {
        // 尾部相邻的同类系统提示去重（避免重复“通话已结束”）
        if item.kind == "system", transcript.last?.text == item.text {
            return
        }
        transcript.append(item)
    }

    // MARK: 记录应用

    /// 用 REST 返回的通话记录更新状态
    private func applyRecord(_ record: VoiceCallRecord) {
        if let id = callID, record.id != id { return }
        callID = record.id
        startedAt = record.started_at
        switch record.statusEnum {
        case .ringing:
            phase = .ringing
        case .active:
            phase = .active
        case .ended:
            phase = .ended
        case .idle:
            break
        }
        bargeInActive = record.isBargeInActive
        if let turn = record.current_turn { currentTurn = turn }
    }

    /// 从 WS `call.state_changed` 的 JSONValue data 手工映射状态视图
    private func decodeStateView(_ data: [String: JSONValue]) -> VoiceCallStateView? {
        VoiceCallStateView(
            call_id: data["call_id"]?.stringValue,
            character_id: data["character_id"]?.stringValue,
            user_id: data["user_id"]?.stringValue,
            direction: data["direction"]?.stringValue,
            status: data["status"]?.stringValue,
            started_at: data["started_at"]?.doubleValue,
            ended_at: data["ended_at"]?.doubleValue,
            duration_sec: data["duration_sec"]?.doubleValue.map { Int($0) },
            ended_reason: data["ended_reason"]?.stringValue
        )
    }

    // MARK: assistant 语音播放

    /// 播放 assistant 音频（AVAudioPlayer 播放内存 Data；@MainActor 线程安全）
    private func playAssistantAudio(data: Data) {
        // 用户正在录音时不播（barge-in 语义：用户声音优先）
        guard !isRecording else { return }
        stopPlayback()
        do {
            let player = try AVAudioPlayer(data: data)
            player.delegate = playbackDelegate
            audioPlayer = player
            isPlayingAudio = true
            player.play()
        } catch {
            presentError(loc("音频播放失败"))
        }
    }

    /// 停止当前播放（barge-in / 新播放替换 / 通话清理时调用）
    private func stopPlayback() {
        audioPlayer?.stop()
        audioPlayer = nil
        isPlayingAudio = false
    }

    // MARK: 辅助

    private func reset() {
        audioRecorder.cancel()
        stopPlayback()
        callID = nil
        characterID = nil
        characterName = nil
        direction = nil
        startedAt = nil
        transcript = []
        bargeInActive = false
        currentTurn = 0
        isRecording = false
        seenSpeechKeys.removeAll()
        seenEventIDs.removeAll()
        phase = .idle
    }

    private func presentError(_ message: String) {
        errorMessage = message
        showError = true
    }

    private func presentError(_ error: Error) {
        if let apiError = error as? APIError {
            presentError(apiError.message)
        } else {
            presentError(error.localizedDescription)
        }
    }
}

// MARK: - 播放完成回调桥接

/// AVAudioPlayerDelegate 回调桥接：`audioPlayerDidFinishPlaying` 由 AVFoundation
/// 在主线程回调，但协议本身非隔离；用普通 NSObject 承接后经闭包跳回 @MainActor。
private final class VoiceCallAudioPlaybackDelegate: NSObject, AVAudioPlayerDelegate {
    var onFinish: (() -> Void)?

    func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        onFinish?()
    }
}
