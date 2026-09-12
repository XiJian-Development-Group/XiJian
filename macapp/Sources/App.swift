import SwiftUI
import XiJianKit

@main
struct XiJianApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var appDelegate

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environment(CoreManager.shared)
                .environment(ThemeSettings.shared)
                .environment(AppViewModel.shared)
                .environment(UserProfileSettings.shared)
                .environment(BackgroundSettings.shared)
        }
        .windowStyle(.hiddenTitleBar)
        .windowToolbarStyle(.unified)
        .defaultSize(width: 960, height: 640)
        .commands {
            // 仅替换「新建窗口」菜单；保留系统默认的退出菜单与 Cmd+Q 快捷键
            //（AppDelegate 的 applicationShouldTerminate 负责停止 Core 子进程）。
            CommandGroup(replacing: .newItem) {}
            // U13：常用操作加 Cmd 快捷键（主窗口内可直接 ⌘R 重启 Core）
            CommandMenu(loc("XiJian")) {
                Button(loc("重启 Core")) {
                    Task { await CoreManager.shared.restartCore() }
                }
                .keyboardShortcut("r", modifiers: .command)

                Button(loc("查看日志")) {
                    AppDelegate.showLogsAction()
                }
                .keyboardShortcut("l", modifiers: .command)
            }
        }
    }
}

@MainActor
class AppDelegate: NSObject, NSApplicationDelegate {
    var statusItem: NSStatusItem?
    var coreManager = CoreManager.shared

    /// 持有的通知观察者 token（仅在 AppDelegate 存活期有效）
    private var notificationObservers: [NSObjectProtocol] = []

    func applicationDidFinishLaunching(_ notification: Notification) {
        // Set up menu bar item
        setupStatusItem()

        // 启动 Touch Bar 控制器（仅在带 Touch Bar 的机型上有可见硬件）
        TouchBarController.shared.startObserving()
        // SwiftUI 的主窗口延迟创建，监听首次出现后挂上
        let windowObserver = NotificationCenter.default.addObserver(
            forName: NSWindow.didBecomeKeyNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            // addObserver(queue: .main) 已在主线程上调度，但编译器看不到；
            // 用 MainActor.assumeIsolated 桥接 @MainActor 类方法
            MainActor.assumeIsolated {
                self?.attachTouchBarToFirstWindow()
            }
        }
        notificationObservers.append(windowObserver)

        // 恢复用户已开启的后台活动（防 App Nap）；引导完成时会同步一次，
        // 这里兜底保证 App 重启后开关状态依然生效
        AppPermissions.shared.syncBackgroundActivity(UserProfileSettings.shared.backgroundActivityEnabled)

        // Start Core on launch
        Task {
            await coreManager.startCore()
        }

        // 监听 Touch Bar → AppDelegate 的全局动作（每个名称单独订阅，避免命中所有通知）
        let restartObserver = NotificationCenter.default.addObserver(
            forName: .xijianRequestRestartCore,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            guard let self else { return }
            Task { await self.coreManager.restartCore() }
        }
        notificationObservers.append(restartObserver)

        let logsObserver = NotificationCenter.default.addObserver(
            forName: .xijianRequestViewLogs,
            object: nil,
            queue: .main
        ) { _ in
            // addObserver(queue: .main) 已在主线程上调度，但编译器看不到；
            // 用 MainActor.assumeIsolated 桥接静态 @MainActor 方法
            MainActor.assumeIsolated {
                Self.showLogsAction()
            }
        }
        notificationObservers.append(logsObserver)
    }

    func applicationWillTerminate(_ notification: Notification) {
        // 应用退出时同步停止 Core 子进程（异步任务不会等待）
        coreManager.stopCoreSync()
        AppPermissions.shared.stopBackgroundActivity()
        TouchBarController.shared.stopObserving()
        for token in notificationObservers {
            NotificationCenter.default.removeObserver(token)
        }
        notificationObservers.removeAll()
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        coreManager.stopCoreSync()
        AppPermissions.shared.stopBackgroundActivity()
        return .terminateNow
    }

    /// SwiftUI 主窗口首次成为 key window 时，挂上 Touch Bar。
    /// 仅第一次挂上即可；之后由窗口本身保留。
    private func attachTouchBarToFirstWindow() {
        guard let window = NSApp.windows.first(where: { $0.isVisible }) else { return }
        if window.touchBar == nil {
            TouchBarController.shared.attach(to: window)
        }
    }

    func setupStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)

        if let button = statusItem?.button {
            button.image = NSImage(systemSymbolName: "bubble.left.and.bubble.right.fill", accessibilityDescription: "XiJian")
            button.image?.isTemplate = true
        }

        let menu = NSMenu()

        let openItem = NSMenuItem(title: loc("打开 XiJian"), action: #selector(openMainWindow), keyEquivalent: "o")
        openItem.target = self
        menu.addItem(openItem)

        menu.addItem(NSMenuItem.separator())

        let restartCoreItem = NSMenuItem(title: loc("重启 Core"), action: #selector(restartCore), keyEquivalent: "r")
        restartCoreItem.target = self
        menu.addItem(restartCoreItem)

        let showLogsItem = NSMenuItem(title: loc("查看日志"), action: #selector(showLogs), keyEquivalent: "l")
        showLogsItem.target = self
        menu.addItem(showLogsItem)

        menu.addItem(NSMenuItem.separator())

        let quitItem = NSMenuItem(title: loc("退出 XiJian"), action: #selector(quitApp), keyEquivalent: "q")
        quitItem.target = self
        menu.addItem(quitItem)

        statusItem?.menu = menu
    }

    @objc func openMainWindow() {
        NSApp.activate(ignoringOtherApps: true)
        for window in NSApp.windows {
            window.makeKeyAndOrderFront(self)
        }
    }

    @objc func restartCore() {
        Task {
            await coreManager.restartCore()
        }
    }

    @objc func showLogs() {
        Self.showLogsAction()
    }

    /// 打开 Core 日志目录（静态版，供主菜单快捷键复用）
    @MainActor
    static func showLogsAction() {
        let logDir = CoreManager.shared.coreDirectory?.appendingPathComponent("logs")
        if let logDir = logDir, FileManager.default.fileExists(atPath: logDir.path) {
            NSWorkspace.shared.open(logDir)
        }
    }

    @objc func quitApp() {
        NSApp.terminate(nil)
    }
}
