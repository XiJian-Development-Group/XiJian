import SwiftUI
import XiJianManagerKit

@main
struct XiJianManagerApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var appDelegate

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environment(AppLifecycleManager.shared)
                .environment(ThemeSettings.shared)
                .environment(UserProfileSettings.shared)
                .environment(BackgroundSettings.shared)
        }
        .windowStyle(.hiddenTitleBar)
        .windowToolbarStyle(.unified)
        .defaultSize(width: 960, height: 640)
        .commands {
            // 仅替换「新建窗口」菜单；保留系统默认的退出菜单与 Cmd+Q 快捷键
            CommandGroup(replacing: .newItem) {}
            // 常用操作加 Cmd 快捷键（主窗口内可直接 ⌘R 重启 Hermes）
            CommandMenu(loc("XiJian Manager")) {
                Button(loc("重启 Hermes")) {
                    Task { try? await AppLifecycleManager.shared.restartGateway() }
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
final class AppDelegate: NSObject, NSApplicationDelegate {
    var statusItem: NSStatusItem?
    var lifecycle = AppLifecycleManager.shared

    func applicationDidFinishLaunching(_ notification: Notification) {
        setupStatusItem()
        Task {
            await lifecycle.ensureHermesInstalledAndRunning()
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        lifecycle.stopGatewaySync()
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        lifecycle.stopGatewaySync()
        return .terminateNow
    }

    func setupStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)

        if let button = statusItem?.button {
            button.image = NSImage(
                systemSymbolName: "shippingbox.fill",
                accessibilityDescription: "XiJian Manager"
            )
            button.image?.isTemplate = true
        }

        let menu = NSMenu()

        let openItem = NSMenuItem(title: loc("打开 XiJian Manager"), action: #selector(openMainWindow), keyEquivalent: "o")
        openItem.target = self
        menu.addItem(openItem)

        menu.addItem(NSMenuItem.separator())

        let startItem = NSMenuItem(title: loc("启动 Hermes"), action: #selector(startHermes), keyEquivalent: "s")
        startItem.target = self
        menu.addItem(startItem)

        let stopItem = NSMenuItem(title: loc("停止 Hermes"), action: #selector(stopHermes), keyEquivalent: "x")
        stopItem.target = self
        menu.addItem(stopItem)

        let restartItem = NSMenuItem(title: loc("重启 Hermes"), action: #selector(restartHermes), keyEquivalent: "r")
        restartItem.target = self
        menu.addItem(restartItem)

        menu.addItem(NSMenuItem.separator())

        let showLogsItem = NSMenuItem(title: loc("查看日志"), action: #selector(showLogs), keyEquivalent: "l")
        showLogsItem.target = self
        menu.addItem(showLogsItem)

        menu.addItem(NSMenuItem.separator())

        let quitItem = NSMenuItem(title: loc("退出"), action: #selector(quitApp), keyEquivalent: "q")
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

    @objc func startHermes() {
        Task { try? await lifecycle.startGateway() }
    }

    @objc func stopHermes() {
        Task { try? await lifecycle.stopGateway() }
    }

    @objc func restartHermes() {
        Task { try? await lifecycle.restartGateway() }
    }

    @objc func showLogs() {
        Self.showLogsAction()
    }

    /// 打开 Hermes 日志目录（静态版，供主菜单快捷键复用）
    @MainActor
    static func showLogsAction() {
        let logDir = AppLifecycleManager.shared.logsDirectory
        if FileManager.default.fileExists(atPath: logDir.path) {
            NSWorkspace.shared.open(logDir)
        }
    }

    @objc func quitApp() {
        NSApp.terminate(nil)
    }
}
