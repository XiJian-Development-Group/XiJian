import AppKit
import Foundation

/// Touch Bar 控制器：把 `NSTouchBar` 桥接到主窗口。
///
/// 设计要点：
/// - 实时反映 Core 运行状态、当前侧边栏标签、聊天流式状态。
/// - 通过 `NotificationCenter` 与主窗口 UI 解耦通信，
///   `ChatView` / `AppDelegate` 监听后触发真实业务逻辑。
/// - 选用 AppKit `NSTouchBar`（SwiftUI 无原生 Touch Bar API），
///   `AppDelegate` 把 `touchBar` 挂到 `NSWindow.touchBar` 即生效。
/// - 仅在带 Touch Bar 的 MacBook Pro 上有可见硬件；
///   其他机型静默失效，不影响功能。

// MARK: - Notification 名称

public extension Notification.Name {
    /// Touch Bar → ChatView：触发「发送当前输入」动作
    static let xijianRequestSendDraft = Notification.Name("com.skyc8266.xijian.request.sendDraft")
    /// Touch Bar → ChatView：触发「停止当前生成」动作
    static let xijianRequestStopStreaming = Notification.Name("com.skyc8266.xijian.request.stopStreaming")
    /// Touch Bar → ChatView：触发「新开会话」动作
    static let xijianRequestNewChatSession = Notification.Name("com.skyc8266.xijian.request.newChatSession")
    /// Touch Bar → AppDelegate：触发「查看 Core 日志」动作
    static let xijianRequestViewLogs = Notification.Name("com.skyc8266.xijian.request.viewLogs")
    /// Touch Bar → AppDelegate：触发「重启 Core」动作
    static let xijianRequestRestartCore = Notification.Name("com.skyc8266.xijian.request.restartCore")
    /// ChatView → Touch Bar：聊天流式状态变化（userInfo: ["isStreaming": Bool]）
    static let xijianChatStateChanged = Notification.Name("com.skyc8266.xijian.chat.stateChanged")
}

@MainActor
public final class TouchBarController: NSObject, NSTouchBarDelegate {

    public static let shared = TouchBarController()

    // MARK: - Item Identifier

    private enum ItemID {
        static let tabSegmented = NSTouchBarItem.Identifier("com.skyc8266.xijian.touchbar.tabSegmented")
        static let coreStatus = NSTouchBarItem.Identifier("com.skyc8266.xijian.touchbar.coreStatus")
        static let newSession = NSTouchBarItem.Identifier("com.skyc8266.xijian.touchbar.newSession")
        static let sendOrStop = NSTouchBarItem.Identifier("com.skyc8266.xijian.touchbar.sendOrStop")
        static let viewLogs = NSTouchBarItem.Identifier("com.skyc8266.xijian.touchbar.viewLogs")
    }

    /// Touch Bar 上展示的标签集合（按主窗口侧边栏 Tab 顺序）
    private struct TabOption {
        let tab: AppViewModel.Tab
        let label: String
    }
    private let tabOptions: [TabOption] = [
        .init(tab: .chat, label: "对话"),
        .init(tab: .characters, label: "角色"),
        .init(tab: .worlds, label: "世界"),
        .init(tab: .settings, label: "设置"),
    ]

    // MARK: - 公开

    public let touchBar: NSTouchBar

    // MARK: - 内部状态

    private var isStreaming: Bool = false

    /// 已创建的 item 引用，便于直接更新视图（无需重建 Touch Bar）
    private weak var tabSegmentItem: NSCustomTouchBarItem?
    private weak var coreStatusItem: NSCustomTouchBarItem?
    private weak var newSessionItem: NSCustomTouchBarItem?
    private weak var sendOrStopItem: NSCustomTouchBarItem?
    private weak var viewLogsItem: NSCustomTouchBarItem?

    /// 挂到主窗口的 `NSWindow` 弱引用，便于 `applicationDidFinishLaunching` 时定位
    private weak var attachedWindow: NSWindow?

    /// 监听 Core 状态 / 当前标签的 observation 任务
    private var observationTask: Task<Void, Never>?

    // MARK: - 初始化

    private override init() {
        touchBar = NSTouchBar()
        super.init()
        touchBar.delegate = self
        touchBar.defaultItemIdentifiers = [
            ItemID.tabSegmented,
            .flexibleSpace,
            ItemID.coreStatus,
            ItemID.viewLogs,
            .flexibleSpace,
            ItemID.newSession,
            ItemID.sendOrStop,
        ]
        // 仅主窗口需要；不进入用户自定义面板
        touchBar.customizationAllowedItemIdentifiers = []
        touchBar.customizationRequiredItemIdentifiers = []

        // 监听聊天流式状态（ChatViewModel 非单例，由 ChatView 上报）
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(handleChatStateChanged(_:)),
            name: .xijianChatStateChanged,
            object: nil
        )
    }

    /// 启动状态观察循环；挂到主窗口后调用一次
    public func startObserving() {
        guard observationTask == nil else { return }
        observationTask = Task { @MainActor [weak self] in
            await self?.runObservationLoop()
        }
    }

    /// 关闭观察循环（应用退出时）
    public func stopObserving() {
        observationTask?.cancel()
        observationTask = nil
    }

    /// 把 Touch Bar 挂到指定主窗口；同时观察新窗口。
    public func attach(to window: NSWindow) {
        window.touchBar = touchBar
        attachedWindow = window
    }

    // MARK: - NSTouchBarDelegate

    public func touchBar(_ touchBar: NSTouchBar, makeItemForIdentifier identifier: NSTouchBarItem.Identifier) -> NSTouchBarItem? {
        switch identifier {
        case ItemID.tabSegmented:
            let item = NSCustomTouchBarItem(identifier: identifier)
            item.customizationLabel = loc("标签切换")
            let control = NSSegmentedControl(
                labels: tabOptions.map(\.label),
                trackingMode: .selectOne,
                target: self,
                action: #selector(tabSegmentChanged(_:))
            )
            control.segmentStyle = .texturedRounded
            control.controlSize = .regular
            // 立即按当前选中标签同步
            let selectedIndex = tabOptions.firstIndex(where: { $0.tab == AppViewModel.shared.selectedTab }) ?? 0
            control.selectedSegment = selectedIndex
            item.view = control
            tabSegmentItem = item
            return item

        case ItemID.coreStatus:
            let item = NSCustomTouchBarItem(identifier: identifier)
            item.customizationLabel = loc("Core 状态")
            let button = NSButton(title: coreStatusTitle(), target: self, action: #selector(coreStatusTapped(_:)))
            button.setButtonType(.momentaryPushIn)
            button.bezelStyle = .recessed
            button.imagePosition = .imageLeft
            button.image = NSImage(systemSymbolName: "circle.fill", accessibilityDescription: nil)?
                .withSymbolConfiguration(.init(pointSize: 8, weight: .bold))
            button.image?.isTemplate = true
            item.view = button
            coreStatusItem = item
            return item

        case ItemID.viewLogs:
            let item = NSCustomTouchBarItem(identifier: identifier)
            item.customizationLabel = loc("查看日志")
            let button = NSButton(
                title: loc("日志"),
                target: self,
                action: #selector(viewLogsTapped(_:))
            )
            button.setButtonType(.momentaryPushIn)
            button.bezelStyle = .recessed
            button.image = NSImage(systemSymbolName: "doc.text.magnifyingglass", accessibilityDescription: nil)
            button.image?.isTemplate = true
            item.view = button
            viewLogsItem = item
            return item

        case ItemID.newSession:
            let item = NSCustomTouchBarItem(identifier: identifier)
            item.customizationLabel = loc("新会话")
            let button = NSButton(
                title: loc("新会话"),
                target: self,
                action: #selector(newSessionTapped(_:))
            )
            button.setButtonType(.momentaryPushIn)
            button.bezelStyle = .recessed
            button.image = NSImage(systemSymbolName: "plus.bubble", accessibilityDescription: nil)
            button.image?.isTemplate = true
            item.view = button
            newSessionItem = item
            return item

        case ItemID.sendOrStop:
            let item = NSCustomTouchBarItem(identifier: identifier)
            item.customizationLabel = loc("发送 / 停止")
            let button = NSButton(
                title: sendOrStopTitle(),
                target: self,
                action: #selector(sendOrStopTapped(_:))
            )
            button.setButtonType(.momentaryPushIn)
            button.bezelStyle = .recessed
            button.image = NSImage(systemSymbolName: sendOrStopIconName(), accessibilityDescription: nil)
            button.image?.isTemplate = true
            item.view = button
            sendOrStopItem = item
            return item

        default:
            return nil
        }
    }

    // MARK: - Action 转发（Target = self）

    @objc private func tabSegmentChanged(_ sender: NSSegmentedControl) {
        let idx = sender.selectedSegment
        guard idx >= 0, idx < tabOptions.count else { return }
        let target = tabOptions[idx].tab
        AppViewModel.shared.selectedTab = target
    }

    @objc private func coreStatusTapped(_ sender: NSButton) {
        // 点击状态：优先重启 Core（用户最常用操作）
        NotificationCenter.default.post(name: .xijianRequestRestartCore, object: nil)
    }

    @objc private func viewLogsTapped(_ sender: NSButton) {
        NotificationCenter.default.post(name: .xijianRequestViewLogs, object: nil)
    }

    @objc private func newSessionTapped(_ sender: NSButton) {
        NotificationCenter.default.post(name: .xijianRequestNewChatSession, object: nil)
    }

    @objc private func sendOrStopTapped(_ sender: NSButton) {
        if isStreaming {
            NotificationCenter.default.post(name: .xijianRequestStopStreaming, object: nil)
        } else {
            NotificationCenter.default.post(name: .xijianRequestSendDraft, object: nil)
        }
    }

    @objc private func handleChatStateChanged(_ note: Notification) {
        if let value = note.userInfo?["isStreaming"] as? Bool {
            isStreaming = value
        } else {
            isStreaming = false
        }
        refreshSendOrStop()
        refreshContextualItems()
    }

    // MARK: - 状态刷新

    /// `withObservationTracking` 循环：每次状态变化后重新注册。
    private func runObservationLoop() async {
        while !Task.isCancelled {
            // 读取关心的状态，触发 onChange
            await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
                withObservationTracking {
                    _ = AppViewModel.shared.selectedTab
                    _ = CoreManager.shared.state
                } onChange: {
                    continuation.resume()
                }
            }
            // 状态已变 → 刷新
            refresh()
        }
    }

    /// 主刷新入口：当前标签、Core 状态、上下文可见性
    private func refresh() {
        refreshTabSegment()
        refreshCoreStatus()
        refreshContextualItems()
        refreshSendOrStop()
    }

    private func refreshTabSegment() {
        guard let view = tabSegmentItem?.view as? NSSegmentedControl else { return }
        let current = AppViewModel.shared.selectedTab
        if let idx = tabOptions.firstIndex(where: { $0.tab == current }) {
            view.selectedSegment = idx
        }
    }

    private func refreshCoreStatus() {
        guard let view = coreStatusItem?.view as? NSButton else { return }
        view.title = coreStatusTitle()
        view.image = NSImage(systemSymbolName: "circle.fill", accessibilityDescription: nil)?
            .withSymbolConfiguration(.init(pointSize: 8, weight: .bold))
        view.image?.isTemplate = true
        // 错误态用红色 tint
        if case .error = CoreManager.shared.state {
            view.contentTintColor = .systemRed
        } else {
            view.contentTintColor = nil
        }
    }

    private func refreshContextualItems() {
        // 聊天专属按钮（新建会话 / 发送）仅在聊天页可见
        let onChat = AppViewModel.shared.selectedTab == .chat
        newSessionItem?.view.isHidden = !onChat
        sendOrStopItem?.view.isHidden = !onChat
    }

    private func refreshSendOrStop() {
        guard let view = sendOrStopItem?.view as? NSButton else { return }
        view.title = sendOrStopTitle()
        view.image = NSImage(systemSymbolName: sendOrStopIconName(), accessibilityDescription: nil)
        view.image?.isTemplate = true
    }

    // MARK: - 文案 / 图标工具

    private func coreStatusTitle() -> String {
        switch CoreManager.shared.state {
        case .stopped: return loc("Core: 未运行")
        case .extracting, .starting: return loc("Core: 启动中")
        case .running: return loc("Core: 运行中")
        case .customServer: return loc("Core: 自定义")
        case .error: return loc("Core: 错误")
        }
    }

    private func sendOrStopTitle() -> String {
        isStreaming ? loc("停止") : loc("发送")
    }

    private func sendOrStopIconName() -> String {
        isStreaming ? "stop.fill" : "paperplane.fill"
    }
}
