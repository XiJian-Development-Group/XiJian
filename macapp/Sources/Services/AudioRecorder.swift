import AVFoundation
import Foundation

// MARK: - 麦克风录音器（A6 真实语音输入）

/// 麦克风录音器：`AVAudioRecorder` 录制 16kHz 单声道 16-bit PCM 到临时 WAV 文件，
/// `stop()` 返回完整音频 Data（供 `POST .../speech` 的 `audio_base64` 上传）。
///
/// 权限：部署目标 macOS 14.0，使用 `AVAudioApplication.requestRecordPermission`
/// （无需兼容旧 API）。录音失败路径统一抛出 `RecordingError`（可展示本地化文案）。
@MainActor
final class AudioRecorder {

    /// 录音错误（本地化可展示）
    enum RecordingError: LocalizedError {
        /// 用户未授予麦克风权限
        case permissionDenied
        /// 录音器启动失败（AVAudioRecorder 初始化 / prepare / record 失败）
        case startFailed(String)
        /// 停止录音后无法读取音频文件
        case readFailed

        var errorDescription: String? { message }

        var message: String {
            switch self {
            case .permissionDenied:
                return loc("麦克风权限被拒绝。请在「系统设置 → 隐私与安全性 → 麦克风」中允许访问。")
            case .startFailed(let detail):
                return loc("录音失败：%@", detail)
            case .readFailed:
                return loc("无法读取录音数据")
            }
        }
    }

    /// 是否正在录音
    var isRecording: Bool { recorder?.isRecording ?? false }

    private var recorder: AVAudioRecorder?
    private var recordURL: URL?

    /// 请求麦克风权限：已授权直接返回 true；未决定时弹系统授权框；
    /// 已拒绝 / 未知状态返回 false（UI 提示跳系统设置）。
    func requestPermission() async -> Bool {
        switch AVAudioApplication.shared.recordPermission {
        case .granted:
            return true
        case .undetermined:
            return await withCheckedContinuation { continuation in
                AVAudioApplication.requestRecordPermission { granted in
                    continuation.resume(returning: granted)
                }
            }
        default:
            return false
        }
    }

    /// 开始录音（需先请求权限）。失败抛 `RecordingError`。
    func start() throws {
        stopInternal()  // 清理上一次未停止的录音

        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("xijian-voice-\(UUID().uuidString).wav")
        // 16kHz 单声道 16-bit PCM —— 服务端 STT（whisper 系）的标准输入格式
        let settings: [String: Any] = [
            AVFormatIDKey: Int(kAudioFormatLinearPCM),
            AVSampleRateKey: 16000,
            AVNumberOfChannelsKey: 1,
            AVLinearPCMBitDepthKey: 16,
            AVLinearPCMIsFloatKey: false,
            AVLinearPCMIsBigEndianKey: false,
        ]

        let recorder: AVAudioRecorder
        do {
            recorder = try AVAudioRecorder(url: url, settings: settings)
        } catch {
            throw RecordingError.startFailed(error.localizedDescription)
        }
        guard recorder.prepareToRecord() else {
            throw RecordingError.startFailed("AVAudioRecorder.prepareToRecord() = false")
        }
        guard recorder.record() else {
            throw RecordingError.startFailed("AVAudioRecorder.record() = false")
        }
        self.recorder = recorder
        self.recordURL = url
    }

    /// 停止录音并返回音频 Data（同时清理临时文件）。未在录音时返回 nil。
    func stop() -> Data? {
        guard let recorder, let url = recordURL else { return nil }
        recorder.stop()
        self.recorder = nil
        self.recordURL = nil
        defer { try? FileManager.default.removeItem(at: url) }
        return try? Data(contentsOf: url)
    }

    /// 丢弃当前录音（不返回数据）
    func cancel() {
        stopInternal()
    }

    // MARK: 内部

    private func stopInternal() {
        guard let recorder, let url = recordURL else { return }
        recorder.stop()
        self.recorder = nil
        self.recordURL = nil
        try? FileManager.default.removeItem(at: url)
    }
}
