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
        }
        .windowStyle(.hiddenTitleBar)
        .windowToolbarStyle(.unified)
        .commands {
            // 仅替换「新建窗口」菜单；保留系统默认的退出菜单与 Cmd+Q 快捷键
            //（AppDelegate 的 applicationShouldTerminate 负责停止 Core 子进程）。
            CommandGroup(replacing: .newItem) {}
        }
    }
}

@MainActor
class AppDelegate: NSObject, NSApplicationDelegate {
    var statusItem: NSStatusItem?
    var coreManager = CoreManager.shared

    func applicationDidFinishLaunching(_ notification: Notification) {
        // Set up menu bar item
        setupStatusItem()

        // Start Core on launch
        Task {
            await coreManager.startCore()
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        // 应用退出时同步停止 Core 子进程（异步任务不会等待）
        coreManager.stopCoreSync()
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        coreManager.stopCoreSync()
        return .terminateNow
    }

    func setupStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)

        if let button = statusItem?.button {
            button.image = NSImage(systemSymbolName: "bubble.left.and.bubble.right.fill", accessibilityDescription: "XiJian")
            button.image?.isTemplate = true
        }

        let menu = NSMenu()

        let openItem = NSMenuItem(title: "打开 XiJian", action: #selector(openMainWindow), keyEquivalent: "o")
        openItem.target = self
        menu.addItem(openItem)

        menu.addItem(NSMenuItem.separator())

        let restartCoreItem = NSMenuItem(title: "重启 Core", action: #selector(restartCore), keyEquivalent: "r")
        restartCoreItem.target = self
        menu.addItem(restartCoreItem)

        let showLogsItem = NSMenuItem(title: "查看日志", action: #selector(showLogs), keyEquivalent: "l")
        showLogsItem.target = self
        menu.addItem(showLogsItem)

        menu.addItem(NSMenuItem.separator())

        let quitItem = NSMenuItem(title: "退出 XiJian", action: #selector(quitApp), keyEquivalent: "q")
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
        let logDir = CoreManager.shared.coreDirectory?.appendingPathComponent("logs")
        if let logDir = logDir, FileManager.default.fileExists(atPath: logDir.path) {
            NSWorkspace.shared.open(logDir)
        }
    }

    @objc func quitApp() {
        NSApp.terminate(nil)
    }
}
